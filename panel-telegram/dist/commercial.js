'use strict';

async function commercialRequest(path, method='GET', body){
  return request(path, method, body);
}

function setTelegramStep(status){
  $('telegram-connected').hidden=!status.authorized;
  $('telegram-phone-step').hidden=status.authorized||!!status.pending_phone;
  $('telegram-code-step').hidden=status.authorized||!status.pending_phone||status.needs_password;
  $('telegram-password-step').hidden=status.authorized||!status.needs_password;
  if(status.authorized){
    $('telegram-name').textContent=status.display_name||'Compte Telegram connecté';
    $('telegram-username').textContent=status.username?'@'+status.username:`ID ${status.user_id||''}`;
    $('telegram-summary').textContent='Ce compte est prêt à recevoir et envoyer les messages du relais.';
  }else if(status.needs_password){
    $('telegram-summary').textContent='Telegram demande votre mot de passe 2FA pour terminer la connexion.';
  }else if(status.pending_phone){
    $('telegram-summary').textContent=`Un code a été envoyé pour ${status.pending_phone}.`;
  }else{
    $('telegram-summary').textContent='Connectez le compte Telegram que ce relais doit gérer.';
  }
}

async function refreshCommercialAccount(){
  if(state.demo||!credentials)return;
  const [status, license]=await Promise.all([
    commercialRequest('/api/telegram/status'),
    commercialRequest('/api/commercial/license'),
  ]);
  setTelegramStep(status);
  $('license-plan').textContent=(license.plan||'licence').toUpperCase();
  const expiry=license.expires_at?new Date(license.expires_at).toLocaleDateString('fr-FR'):'sans expiration';
  $('license-details').textContent=`${license.max_accounts} compte(s) autorisé(s) · ${expiry}`;
  state.connected=!!status.authorized;
  render();
}

async function openTelegramDialog(){
  if(state.demo){toast('Connectez-vous à votre vrai espace pour ajouter Telegram.');return;}
  $('telegram-error').textContent='';
  $('telegram-dialog').showModal();
  try{await refreshCommercialAccount();}catch(error){$('telegram-error').textContent=error.message||'Impossible de charger le compte Telegram.';}
}

$('connect-open').addEventListener('click',event=>{event.preventDefault();openTelegramDialog();});
$('connect-banner').addEventListener('click',event=>{event.preventDefault();if(state.demo){showLogin();return;}openTelegramDialog();});

$('telegram-send-code').addEventListener('click',()=>busy($('telegram-send-code'),async()=>{
  $('telegram-error').textContent='';
  try{
    await commercialRequest('/api/telegram/send-code','POST',{phone:$('telegram-phone').value.trim()});
    $('telegram-code').value='';
    await refreshCommercialAccount();
  }catch(error){$('telegram-error').textContent=error.message;}
}));

$('telegram-submit-code').addEventListener('click',()=>busy($('telegram-submit-code'),async()=>{
  $('telegram-error').textContent='';
  try{
    const result=await commercialRequest('/api/telegram/code','POST',{code:$('telegram-code').value.trim()});
    if(result.next==='password')$('telegram-2fa').focus();
    await refreshCommercialAccount();
  }catch(error){$('telegram-error').textContent=error.message;}
}));

$('telegram-submit-password').addEventListener('click',()=>busy($('telegram-submit-password'),async()=>{
  $('telegram-error').textContent='';
  try{
    await commercialRequest('/api/telegram/password','POST',{password:$('telegram-2fa').value});
    $('telegram-2fa').value='';
    await refreshCommercialAccount();
    toast('Compte Telegram connecté.');
  }catch(error){$('telegram-error').textContent=error.message;}
}));

$('telegram-disconnect').addEventListener('click',()=>busy($('telegram-disconnect'),async()=>{
  $('telegram-error').textContent='';
  try{
    await commercialRequest('/api/telegram/logout','POST',{});
    await refreshCommercialAccount();
    toast('Compte Telegram déconnecté de cette installation.');
  }catch(error){$('telegram-error').textContent=error.message;}
}));
