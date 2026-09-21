"""Public Telegram lookup with a durable, bounded AI research queue."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
import os
from pathlib import Path
import time
import httpx
from sqlalchemy import func, select, text
from app.ai_client import CompatibleAI, AIError
from app.database import BotState, BotUpdate, ResearchJob, connect
from app.research import NeedsReview, ResearchPipeline
from app.service import KnowledgeService, NotFound, normalize

LOG = logging.getLogger('criteria.bot')
HELP = ('Gửi tên tiêu chuẩn hoặc cách đo CT/MRI/siêu âm để tra cứu. '
        'Nếu chưa có dữ liệu, bot sẽ tìm tài liệu mở và tạo bản AI sơ bộ. '
        'Chỉ gửi chủ đề kiến thức chung, không gửi thông tin nhận dạng bệnh nhân. '
        'Dùng /status để xem yêu cầu gần nhất. Không thay thế đánh giá lâm sàng.')


class Telegram:
    def __init__(self, token):
        if not token or ':' not in token:
            raise ValueError('TELEGRAM_BOT_TOKEN is required')
        self.token = token

    def call(self, method, payload):
        # Never log HTTP URL: Telegram includes the bot token in its path.
        try:
            with httpx.Client(timeout=45, trust_env=False) as client:
                response = client.post('https://api.telegram.org/bot'+self.token+'/'+method, json=payload)
                data = response.json()
                if response.status_code != 200 or not data.get('ok'):
                    raise RuntimeError('TELEGRAM_HTTP_'+str(response.status_code))
                return data['result']
        except (httpx.HTTPError, ValueError):
            raise RuntimeError('TELEGRAM_TRANSPORT_ERROR') from None

    def send(self, chat_id, message):
        # Telegram measures UTF-16; 1800 Unicode codepoints fit even with emoji.
        for start in range(0, len(message), 1800):
            self.call('sendMessage', {'chat_id': chat_id, 'text': message[start:start+1800],
                                      'link_preview_options': {'is_disabled': True}})


def answer(service, card_id):
    published = service.get_published(card_id)
    c, v = published['card'], published['verification']
    status = ('BÁC SĨ ĐÃ DUYỆT' if v['doctor'] == 'DOCTOR_VERIFIED' else 'AI SƠ BỘ — chưa được bác sĩ duyệt')
    lines = [c['name_vi'], f"Revision {published['revision']} · {status}",
             f"AI/Gemini: {v['gemini']} · ChatGPT: {v['chatgpt']} · Doctor: {v['doctor']}",
             'Phiên bản nguồn: '+c['guideline_version'],
             'Đối tượng: '+c['applicability']['population'],
             'Bối cảnh: '+c['applicability']['clinical_context']]
    for claim in c['claims']:
        t = claim.get('threshold')
        lines.append('• '+claim['text_vi']+(f" ({t['parameter']} {t['operator']} {t['value']} {t['unit']})" if t else ''))
    lines.extend(['Logic: '+c['logic']['description'],
                  'Điều kiện: '+'; '.join(c['applicability']['prerequisites']),
                  'Loại trừ: '+'; '.join(c['applicability']['exclusions']),
                  'Dữ liệu cần: '+'; '.join(c['applicability']['required_inputs']),
                  'Giới hạn: '+'; '.join(c['applicability']['limitations'])])
    if c.get('measurement'):
        lines.append('Cách đo: '+'; '.join(str(value) for value in c['measurement'].values()))
    from app.database import Document
    with service.sessions() as db:
        for e in c['evidence']:
            source = db.get(Document, e['document_version_id'])
            lines.append(f"Nguồn: {source.payload['title']} — trang PDF {e['pdf_page']}\n{source.payload['official_url']}")
    message = '\n'.join(lines)
    if len(message) > 14000:
        return '\n'.join(lines[:6])+f'\nNội dung dài; xem đầy đủ qua hệ thống. Mã: {card_id}. Không hiển thị các ngưỡng tách rời điều kiện áp dụng.'
    return message


class BotQueue:
    def __init__(self, sessions, *, daily_limit=20, cooldown=60, attempts=2):
        self.sessions = sessions
        self.service = KnowledgeService(sessions)
        self.daily_limit, self.cooldown, self.max_attempts = daily_limit, cooldown, attempts

    def offset(self):
        with self.sessions() as db:
            state = db.get(BotState, 'telegram')
            return state.offset if state else 0

    def receive(self, update):
        now = int(time.time())
        message = update.get('message', {})
        chat_id = str(message.get('chat', {}).get('id', ''))
        query = message.get('text', '').strip()
        with self.sessions.begin() as db:
            state = db.get(BotState, 'telegram')
            if not state:
                state = BotState(id='telegram', offset=0)
                db.add(state)
            state.offset = max(state.offset, update['update_id']+1)
            if db.get(BotUpdate, update['update_id']):
                return
            if not chat_id or message.get('from', {}).get('is_bot') or not query:
                return
            row = BotUpdate(id=update['update_id'], chat_id=chat_id)
            db.add(row)
            if query.split()[0].split('@')[0] in ('/start', '/help'):
                row.reply = HELP
                return
            if query.split()[0].split('@')[0] == '/status':
                prior = db.scalar(select(ResearchJob).join(BotUpdate, BotUpdate.job_id == ResearchJob.id).where(BotUpdate.chat_id == chat_id).order_by(BotUpdate.id.desc()))
                row.reply = f"Yêu cầu: {prior.query}\nTrạng thái: {prior.status}\nMã: {prior.id[:12]}\nChi tiết: {prior.error_code or '—'}" if prior else 'Chưa có yêu cầu tìm kiếm.'
                return
            if query.startswith('/ask '):
                query = query[5:].strip()
            if not 3 <= len(query) <= 500:
                row.reply = 'Gửi tên chủ đề kiến thức từ 3–500 ký tự. '+HELP
                return
            hits = self.service.search(query)
            if len(hits) == 1:
                row.reply = answer(self.service, hits[0]['card_id'])
                return
            if hits:
                row.reply = 'Có nhiều kết quả. Gửi mã tiêu chuẩn cần xem:\n'+'\n'.join(h['card_id']+' — '+h['name_vi'] for h in hits[:10])
                return
            key = hashlib.sha256(normalize(query).encode()).hexdigest()
            job = db.get(ResearchJob, key)
            if job is None:
                total = db.scalar(select(func.count()).select_from(ResearchJob).where(ResearchJob.created_at >= now-86400))
                recent = db.scalar(select(ResearchJob.id).join(BotUpdate, BotUpdate.job_id == ResearchJob.id).where(BotUpdate.chat_id == chat_id, ResearchJob.created_at > now-self.cooldown).limit(1))
                if total >= self.daily_limit or recent:
                    row.reply = 'Đã đạt giới hạn tạo dữ liệu mới tạm thời. Bạn vẫn tra cứu được các tiêu chuẩn đã có; hãy thử chủ đề mới sau.'
                    return
                job = ResearchJob(id=key, query=query, created_at=now)
                db.add(job)
                db.flush()
            row.job_id = job.id

    def claim(self):
        now = int(time.time())
        with self.sessions.begin() as db:
            job = db.scalar(select(ResearchJob).where(ResearchJob.status.in_(['queued', 'retry', 'running']), ResearchJob.available_at <= now).order_by(ResearchJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:
                return None
            if job.attempts >= self.max_attempts:
                job.status, job.error_code = 'failed', 'RETRY_LIMIT'
                return None
            job.status, job.attempts, job.available_at = 'running', job.attempts+1, now+1800
            return job

    def process(self, pipeline):
        job = self.claim()
        if not job:
            return False
        try:
            card_id = pipeline.run(job)
            with self.sessions.begin() as db:
                record = db.get(ResearchJob, job.id)
                record.status, record.card_id, record.error_code = 'completed', card_id, None
        except NeedsReview as exc:
            with self.sessions.begin() as db:
                record = db.get(ResearchJob, job.id)
                record.status, record.error_code = 'needs_review', str(exc)[:100]
        except Exception as exc:
            with self.sessions.begin() as db:
                record = db.get(ResearchJob, job.id)
                record.status = 'retry' if record.attempts < self.max_attempts else 'failed'
                record.available_at = int(time.time())+120
                record.error_code = str(exc)[:100] if isinstance(exc, AIError) else type(exc).__name__[:100]
            LOG.warning('research failed job=%s type=%s', job.id[:12], type(exc).__name__)
        return True

    def deliver(self, telegram):
        with self.sessions() as db:
            completed = select(ResearchJob.id).where(ResearchJob.id == BotUpdate.job_id, ResearchJob.status.in_(['completed', 'failed', 'needs_review'])).exists()
            updates = db.scalars(select(BotUpdate).where(BotUpdate.delivered.is_(False), BotUpdate.send_attempts < 10, BotUpdate.next_send_at <= int(time.time()), (BotUpdate.acknowledged.is_(False)) | completed).order_by(BotUpdate.id).limit(30)).all()
        for row in updates:
            done = True
            if row.reply:
                reply = row.reply
            else:
                with self.sessions() as db:
                    job = db.get(ResearchJob, row.job_id)
                if job.status == 'completed':
                    try:
                        reply = answer(self.service, job.card_id)
                    except NotFound:
                        reply = 'Bản tiêu chuẩn đã được thu hồi hoặc đang cần sửa; chưa có bản công bố để trả lời.'
                elif job.status in ('failed', 'needs_review'):
                    reply = 'Chưa tạo được bản đủ bằng chứng để công bố. Yêu cầu đã được lưu để kiểm tra.\nMã: '+job.id[:12]+'\nTrạng thái: '+job.status+'\nChi tiết: '+(job.error_code or '')
                else:
                    if row.acknowledged:
                        continue
                    done = False
                    reply = 'Đã nhận yêu cầu. Bot đang tìm nguồn và kiểm tra bằng chứng; kết quả AI sơ bộ sẽ được gửi sau.\nMã: '+job.id[:12]
            try:
                telegram.send(row.chat_id, reply)
            except RuntimeError:
                # One blocked chat must not stop the entire public queue.
                with self.sessions.begin() as db:
                    stored = db.get(BotUpdate, row.id)
                    stored.send_attempts += 1
                    stored.next_send_at = int(time.time()) + min(3600, 30 * 2**stored.send_attempts)
                continue
            with self.sessions.begin() as db:
                stored = db.get(BotUpdate, row.id)
                stored.acknowledged = True
                stored.delivered = done
                stored.send_attempts = 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-config', action='store_true')
    parser.add_argument('--check-ai', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    ai = CompatibleAI.from_env()
    telegram = Telegram(os.environ['TELEGRAM_BOT_TOKEN'])
    root = Path(os.getenv('SOURCE_PDF_ROOT', '/source_pdf'))
    if not root.is_dir() or not os.access(root, os.W_OK):
        raise ValueError('SOURCE_PDF_ROOT must be mounted writable')
    if args.check_config:
        print('BOT_CONFIG_OK: public access; OpenAI-compatible HTTPS; writable PDF mount; no network calls')
        return
    if args.check_ai:
        result = ai.ask('Connection test. Return {"ok":true}.', {'test': 'connectivity only; no medical content'})
        if result.get('ok') is not True:
            raise RuntimeError('AI_CHECK_FAILED')
        print('AI_CONNECTION_OK')
        return
    url = os.environ['DATABASE_URL']
    if not url.startswith('postgresql+psycopg://'):
        raise ValueError('production requires PostgreSQL')
    engine, sessions = connect(url)
    # Lifetime singleton prevents two pollers consuming one bot's update stream.
    lock = engine.connect()
    if not lock.execute(text('SELECT pg_try_advisory_lock(2026092201)')).scalar():
        raise RuntimeError('another Telegram worker is active')
    if telegram.call('getWebhookInfo', {}).get('url'):
        raise RuntimeError('Telegram webhook is active; remove it before enabling long polling')
    queue = BotQueue(sessions, daily_limit=int(os.getenv('AI_DAILY_JOB_LIMIT', '20')),
                     cooldown=int(os.getenv('AI_CHAT_COOLDOWN_SECONDS', '60')))
    pipeline = ResearchPipeline(sessions, ai, root)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = None
        try:
            while True:
                try:
                    if future is None or future.done():
                        finished, future = future, None
                        if finished:
                            finished.result()
                        future = executor.submit(queue.process, pipeline)
                    updates = telegram.call('getUpdates', {'offset': queue.offset(), 'timeout': 10, 'limit': 30, 'allowed_updates': ['message']})
                    for update in updates:
                        queue.receive(update)
                    queue.deliver(telegram)
                    Path('/tmp/bot-heartbeat').write_text(str(time.time()))
                except Exception as exc:
                    LOG.error('bot cycle failed type=%s', type(exc).__name__)
                    time.sleep(5)
        finally:
            lock.close()
            engine.dispose()


if __name__ == '__main__':
    main()
