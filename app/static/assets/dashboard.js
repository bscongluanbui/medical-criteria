const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeURL = value => {try {const u=new URL(value);return u.protocol==='https:'?u.href:'#';}catch{return '#';}};
let account, cards=[], sources=[], slots=[], selected=null;
const statusName={pending:'Chờ duyệt',published:'Đã xuất bản',withdrawn:'Đã thu hồi'};
function notice(text,error=false,target='#message'){const box=$(target);box.textContent=text;box.classList.remove('hidden');box.classList.toggle('error',error);}
async function api(path,body){
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(account?{'X-CSRF-Token':account.csrf}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const result=await response.json();
  if(response.status===401){location.assign('/login');throw new Error('Phiên đăng nhập đã hết hạn.');}
  if(!response.ok){const detail=Array.isArray(result.detail)?result.detail.map(x=>`${x.loc.join('.')}: ${x.msg}`).join('\n'):result.detail;throw new Error(detail||'Thao tác chưa thành công.');}
  return result;
}
async function action(button,fn,target='#message'){button.disabled=true;try{await fn();}catch(e){notice(e.message,true,target);}finally{button.disabled=false;}}
function setView(view){document.querySelectorAll('[id^="view-"]').forEach(el=>el.classList.toggle('hidden',el.id!==`view-${view}`));document.querySelectorAll('.side-link').forEach(el=>el.classList.toggle('active',el.dataset.view===view));}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>setView(el.dataset.view)));
document.querySelectorAll('[data-close]').forEach(el=>el.addEventListener('click',()=>document.getElementById(el.dataset.close).close()));
async function refresh(){[cards,sources,slots]=await Promise.all(['/web/cards','/web/sources','/web/featured'].map(async p=>(await api(p)).results));render();}
function render(){
  $('#stats').innerHTML=[['Tổng tiêu chuẩn',cards.length],['Chờ kiểm duyệt',cards.filter(c=>c.status==='pending').length],['Có bản xuất bản',cards.filter(c=>c.published).length]].map(([label,n])=>`<div class="stat"><small>${label}</small><strong>${n.toString().padStart(2,'0')}</strong></div>`).join('');
  renderRows();renderSources();renderFeatured();
}
function renderRows(){
  const q=$('#search').value.toLocaleLowerCase('vi'),filter=$('#filter').value;
  const items=cards.filter(c=>(filter==='all'||c.status===filter)&&`${c.id} ${c.card.name_vi} ${c.card.name_en} ${c.card.modality}`.toLocaleLowerCase('vi').includes(q));
  $('#rows').innerHTML=items.map(c=>`<tr><td><strong>${esc(c.card.name_vi)}</strong><small>${esc(c.id)}</small></td><td>${esc(c.card.modality)}</td><td>r${c.latest}<small>Guideline ${esc(c.card.guideline_version)}</small></td><td><span class="badge ${c.status}">${statusName[c.status]}</span></td><td><button class="button small outline" data-card="${esc(c.id)}">Mở →</button></td></tr>`).join('');
  $('#empty-cards').classList.toggle('hidden',items.length>0);
  document.querySelectorAll('[data-card]').forEach(button=>button.addEventListener('click',()=>openCard(cards.find(c=>c.id===button.dataset.card))));
}
function renderSources(){
  $('#sources-list').innerHTML=sources.length?sources.map(s=>`<article class="source-card"><span class="pill">${esc(s.id)}</span><h3>${esc(s.title)}</h3><p class="muted">${esc(s.organization)} · ${esc(s.version)} · ${s.pdf_pages} trang</p><p class="fine-print">SHA256<br><code>${esc(s.sha256)}</code></p><a href="${esc(safeURL(s.official_url))}" target="_blank" rel="noopener noreferrer">Mở nguồn chính thức ↗</a></article>`).join(''):'<div class="empty"><h3>Chưa có tài liệu nguồn</h3><p>Quản trị viên đăng ký tài liệu gốc để bắt đầu biên soạn.</p></div>';
}
function renderFeatured(){
  $('#featured-list').innerHTML=Array.from({length:6},(_,i)=>{
    const slot=slots.find(s=>s.slot===i+1), published=cards.filter(c=>c.published);
    const current=slot?.card_id?cards.find(c=>c.id===slot.card_id):null;
    const stale=slot?.card_id&&(!current||current.published!==slot.revision);
    return `<div class="featured-row"><span class="featured-number">0${i+1}</span><div><label for="slot-${i+1}">Vị trí ${i+1}${stale?' · Bản cũ đang được ẩn':''}</label><select id="slot-${i+1}" ${account.role==='admin'?'':'disabled'}><option value="">Không hiển thị</option>${published.map(c=>`<option value="${esc(c.id)}" ${!stale&&c.id===slot?.card_id?'selected':''}>${esc(c.card.name_vi)} · bản xuất bản r${c.published}</option>`).join('')}</select></div><button class="button outline" data-slot="${i+1}" ${account.role==='admin'?'':'disabled'}>Lưu vị trí</button></div>`;
  }).join('');
  document.querySelectorAll('[data-slot]').forEach(button=>button.addEventListener('click',()=>action(button,async()=>{const slot=Number(button.dataset.slot),id=$(`#slot-${slot}`).value,c=cards.find(c=>c.id===id);await api('/web/featured',{slot,card_id:id||null,revision:c?.published||null});await refresh();notice('Đã cập nhật thư viện công khai.');})));
}
function template(){
  const s=sources[0];
  return {schema_version:'1.0',topic_id:'',name_vi:'',name_en:'',aliases:[],type:'diagnostic_criteria',guideline_family:'',guideline_module:'',guideline_version:'',modality:'',origin:'manual',applicability:{population:'',clinical_context:'',intended_use:'',prerequisites:[],exclusions:[],required_inputs:[],missing_data_policy:'insufficient_data',discordance_policy:'',limitations:['']},measurement:null,claims:[{id:'claim-1',text_vi:'',evidence_ids:['evidence-1'],threshold:null}],logic:{kind:'reference_only',claim_ids:['claim-1'],minimum:null,description:''},evidence:[{id:'evidence-1',document_version_id:s?.id||'',document_sha256:s?.sha256||'',pdf_page:1,section:'',quote:'',parser_version:'manual'}]};
}
async function openCard(row){
  selected=row?structuredClone(row):{id:'',latest:0,published:null,status:'pending',card:template()};
  $('#editor-title').textContent=row?`Biên tập · revision ${row.latest}`:'Tạo tiêu chuẩn mới';
  $('#editor-error').classList.add('hidden');$('#card-id').value=selected.id;$('#card-id').disabled=!!selected.id;
  $('#card-name').value=selected.card.name_vi;$('#card-english').value=selected.card.name_en;
  $('#card-json').value=JSON.stringify(selected.card,null,2);
  $('#claim-editor').innerHTML=selected.card.claims.map((c,i)=>`<div class="claim-input"><label for="claim-${i}">Claim ${i+1} · ${esc(c.id)}</label><textarea id="claim-${i}" rows="3">${esc(c.text_vi)}</textarea>${c.threshold?`<label>Ngưỡng · ${esc(c.threshold.parameter)}</label><div class="threshold-row"><select aria-label="Toán tử claim ${i+1}" id="operator-${i}">${['<','<=','==','>=','>'].map(v=>`<option ${v===c.threshold.operator?'selected':''}>${esc(v)}</option>`).join('')}</select><input id="value-${i}" aria-label="Giá trị claim ${i+1}" value="${esc(c.threshold.value)}"><input id="unit-${i}" aria-label="Đơn vị claim ${i+1}" value="${esc(c.threshold.unit)}"></div>`:''}</div>`).join('');
  $('#evidence-list').innerHTML=selected.card.evidence.map(e=>{const s=sources.find(s=>s.id===e.document_version_id);return `<div class="evidence-item"><strong>${esc(s?.title||e.document_version_id||'Chưa chọn nguồn')}</strong><p class="fine-print">Trang ${e.pdf_page} · ${esc(e.section||'Chưa có mục')}</p><blockquote>${esc(e.quote||'Điền trích dẫn nguyên văn trong cấu trúc JSON.')}</blockquote>${s?`<a href="${esc(safeURL(s.official_url))}" target="_blank" rel="noopener noreferrer">Mở nguồn gốc ↗</a>`:''}</div>`;}).join('');
  $('#evidence-checked').checked=false;$('#applicability-checked').checked=false;$('#review-reason').value='';
  $('#publish').disabled=!row||row.status==='withdrawn'||row.verification?.doctor==='DOCTOR_VERIFIED';
  $('#verification-status').textContent=row?verificationText(row.verification):'Chưa lưu revision';
  $('#publish-preliminary').disabled=!row||row.status!=='pending';$('#export-audit').disabled=!row;$('#import-audit').disabled=!row;$('#audit-result').value='';$('#gemini-model').value=row?.verification?.gemini_model||'';$('#withdraw').disabled=!row?.published;
  $('#history-list').textContent=row?'Đang tải…':'Chưa có lịch sử.';
  if(!$('#editor').open)$('#editor').showModal();
  if(row){try{const history=await api(`/web/cards/${encodeURIComponent(row.id)}/history`);$('#history-list').innerHTML=history.map(h=>`<div class="history-item"><strong>r${h.revision} · ${esc(h.action)}</strong><br>${esc(h.actor)} · ${esc(new Date(h.at).toLocaleString('vi-VN'))}<p>${esc(h.reason)}</p></div>`).join('');}catch(e){notice(e.message,true,'#editor-error');}}
}
function editedCard(){
  let c;try{c=JSON.parse($('#card-json').value);}catch{throw new Error('JSON chưa hợp lệ. Kiểm tra dấu phẩy và dấu ngoặc.');}
  c.name_vi=$('#card-name').value;c.name_en=$('#card-english').value;
  selected.card.claims.forEach((old,i)=>{const found=c.claims.find(x=>x.id===old.id);if(!found)return;found.text_vi=$(`#claim-${i}`).value;if(found.threshold&&$(`#operator-${i}`)){found.threshold.operator=$(`#operator-${i}`).value;found.threshold.value=$(`#value-${i}`).value;found.threshold.unit=$(`#unit-${i}`).value;}});
  return c;
}
$('#save-draft').addEventListener('click',event=>action(event.currentTarget,async()=>{const id=$('#card-id').value.trim();if(!/^[a-zA-Z0-9_-]{1,100}$/.test(id))throw new Error('Mã tiêu chuẩn chỉ dùng chữ, số, dấu gạch ngang hoặc gạch dưới.');await api(`/web/cards/${id}/revisions`,{expected_revision:selected.latest,card:editedCard()});await refresh();await openCard(cards.find(c=>c.id===id));notice('Đã lưu revision mới. Bản này đang chờ kiểm duyệt.');},'#editor-error'));
$('#publish').addEventListener('click',event=>action(event.currentTarget,async()=>{if(JSON.stringify(editedCard())!==JSON.stringify(selected.card))throw new Error('Có thay đổi chưa lưu. Hãy lưu bản nháp mới trước khi duyệt.');await api(`/web/cards/${selected.id}/publish`,{expected_revision:selected.latest,reason:$('#review-reason').value,evidence_checked:$('#evidence-checked').checked,applicability_checked:$('#applicability-checked').checked});$('#editor').close();await refresh();notice('Đã duyệt và xuất bản. Việc công khai được chọn riêng.');},'#editor-error'));
$('#withdraw').addEventListener('click',event=>action(event.currentTarget,async()=>{if(!confirm(`Thu hồi bản đã xuất bản r${selected.published}? Nội dung này sẽ ngừng xuất hiện công khai.`))return;await api(`/web/cards/${selected.id}/withdraw`,{expected_revision:selected.published,reason:$('#review-reason').value});$('#editor').close();await refresh();notice('Đã thu hồi bản xuất bản.');},'#editor-error'));
$('#new-card').addEventListener('click',()=>openCard(null));$('#refresh').addEventListener('click',e=>action(e.currentTarget,refresh));$('#search').addEventListener('input',renderRows);$('#filter').addEventListener('change',renderRows);
$('#logout').addEventListener('click',e=>action(e.currentTarget,async()=>{await api('/web/logout',{});location.assign('/login');}));
const sourceFields=[['id','Mã phiên bản nguồn','text'],['source_id','Mã tài liệu','text'],['title','Tên tài liệu','text'],['organization','Tổ chức / tác giả','text'],['version','Năm / phiên bản','text'],['doi','DOI (tùy chọn)','text'],['official_url','URL nguồn chính thức (HTTPS)','url'],['sha256','SHA256 của file gốc','text'],['pdf_pages','Số trang PDF','number'],['license_note','Quyền sử dụng / lưu trữ','text'],['archive_reference','Vị trí bản nguồn được lưu giữ','text']];
$('#source-fields').innerHTML=sourceFields.map(([id,label,type])=>`<div><label for="source-${id}">${label}</label><input id="source-${id}" name="${id}" type="${type}" ${id==='doi'?'':'required'} ${type==='number'?'min="1"':''} ${id==='sha256'?'pattern="[a-f0-9]{64}" maxlength="64"':''}></div>`).join('');
$('#new-source').addEventListener('click',()=>{$('#source-error').classList.add('hidden');$('#source-dialog').showModal();});
$('#source-form').addEventListener('submit',event=>{event.preventDefault();action(event.target.querySelector('button[type="submit"]'),async()=>{const data=Object.fromEntries(new FormData(event.target));data.pdf_pages=Number(data.pdf_pages);data.doi=data.doi||null;data.retention='retained_source';await api('/web/sources',data);event.target.reset();$('#source-dialog').close();await refresh();notice('Đã đăng ký phiên bản tài liệu.');},'#source-error');});
(async()=>{try{account=await api('/web/session');$('#account-name').textContent=`${account.email} · ${account.role==='admin'?'Quản trị':'Kiểm duyệt'}`;document.querySelectorAll('.admin-only').forEach(el=>el.classList.toggle('hidden',account.role!=='admin'));await refresh();}catch(e){notice(e.message,true);}})();

function verificationText(v){return `Gemini: ${v?.gemini||'NOT_RECORDED'} · ChatGPT: ${v?.chatgpt||'GPT_UNVERIFIED'} · Bác sĩ: ${v?.doctor||'DOCTOR_UNVERIFIED'}`;}
$('#publish-preliminary').addEventListener('click',e=>action(e.currentTarget,async()=>{
 if(JSON.stringify(editedCard())!==JSON.stringify(selected.card))throw new Error('Lưu thay đổi thành revision mới trước khi công bố.');
 await api(`/web/cards/${selected.id}/publish-preliminary`,{expected_revision:selected.latest,reason:$('#review-reason').value,model:$('#gemini-model').value});
 $('#editor').close();await refresh();notice('Đã công bố bản AI sơ bộ, chưa được bác sĩ duyệt. Chọn vị trí thư viện để hiển thị trên web.');
},'#editor-error'));
$('#export-audit').addEventListener('click',e=>action(e.currentTarget,async()=>{
 if(JSON.stringify(editedCard())!==JSON.stringify(selected.card))throw new Error('Lưu thay đổi trước khi xuất audit.');
 const pkg=await api(`/web/cards/${selected.id}/audit-package`,{expected_revision:selected.latest,reason:'ChatGPT Web audit'});
 const url=URL.createObjectURL(new Blob([JSON.stringify(pkg,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`audit-${pkg.audit_package_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 notice('Đã tải snapshot. Đưa snapshot và đúng tài liệu nguồn lên Google Drive.');
},'#editor-error'));
$('#import-audit').addEventListener('click',e=>action(e.currentTarget,async()=>{
 const result=JSON.parse($('#audit-result').value);if(result.card_id!==selected.id)throw new Error('Kết quả thuộc card khác.');
 const saved=await api('/web/audit-results',result);await refresh();await openCard(cards.find(c=>c.id===selected.id));notice(saved.historical?'Đã lưu audit vào phiên bản cũ, không đổi trạng thái phiên bản mới.':'Đã ghi nhận kết quả ChatGPT audit.');
},'#editor-error'));
