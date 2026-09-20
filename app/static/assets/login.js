const form = document.querySelector('#login-form');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  const error = document.querySelector('#login-error');
  button.disabled = true; button.textContent = 'Đang đăng nhập…'; error.classList.add('hidden');
  try {
    const response = await fetch('/web/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:form.email.value,password:form.password.value})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Vui lòng kiểm tra thông tin đăng nhập.');
    location.assign('/dashboard');
  } catch (e) {error.textContent=e.message;error.classList.remove('hidden');}
  finally {button.disabled=false;button.textContent='Đăng nhập →';}
});
