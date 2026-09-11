'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const initialSettings = {enabled:true,tone:'Réponds en français, avec un ton chaleureux et naturel. Tutoie le client et reste concise. Pour toute demande qui nécessite une décision personnelle, passe-moi la main.',catalog:'',faq:'',daily_limit:100,glossary:[{expression:'dispo',replacement:'disponibilité'}]};
const demoChats = [
 {id:1,name:'Camille',mode:'auto',unread:1,preview:'Super, merci pour les infos !',updated:Date.now()/1000,messages:[{id:1,source:'client',text:'Coucou ! Je voudrais avoir quelques infos sur tes prestations 🙂',created:Date.now()/1000-600},{id:2,source:'ai',text:'Coucou ! Avec plaisir. Tu avais une prestation en particulier en tête ? Dis-moi ce que tu recherches et je te renseigne.',created:Date.now()/1000-550},{id:3,source:'client',text:'Je voulais surtout connaître les différentes possibilités et les tarifs.',created:Date.now()/1000-500},{id:4,source:'ai',text:'Bien sûr ! Je vais te faire préciser tout ça pour te donner les bons tarifs.',created:Date.now()/1000-450},{id:5,source:'client',text:'Super, merci pour les infos !',created:Date.now()/1000-60}]},
 {id:2,name:'Alex',mode:'manual',unread:2,preview:'Tu peux me préciser les conditions ?',updated:Date.now()/1000-900,messages:[{id:6,source:'client',text:'Tu peux me préciser les conditions ?',created:Date.now()/1000-900}]},
 {id:3,name:'Léa',mode:'auto',unread:0,preview:'Merci, je regarde et je te redis.',updated:Date.now()/1000-2700,messages:[{id:7,source:'client',text:'Merci, je regarde et je te redis.',created:Date.now()/1000-2700}]},
 {id:4,name:'Sacha',mode:'manual',unread:0,preview:'Je te réponds dans quelques minutes.',updated:Date.now()/1000-5400,messages:[{id:8,source:'client',text:'J’ai une demande un peu particulière.',created:Date.now()/1000-5600},{id:9,source:'human',text:'Je te réponds dans quelques minutes.',created:Date.now()/1000-5400}]}
];
let state = {demo:false,chats:[],settings:{...initialSettings},connected:false,usage:0};
let sessionTimer = null;
const pendingSends = new Map();
let selected = 1, filter='all', view='inbox', credentials=null, threadMessages=[], lastMessages='', refreshing=false, toastTimer, connectionEpoch=0, glossaryRows=[];
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5000);}
function time(t){return new Date(t*1000).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});}
async function request(path,method='GET',body,auth=credentials){
  if(!auth)throw Error('Connectez-vous à votre espace.');
  const epoch=connectionEpoch;
  const response=await fetch(auth.url+path,{method,headers:{Authorization:'Bearer '+auth.key,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(90000),credentials:'omit',redirect:'error'});
  if(epoch!==connectionEpoch)throw Error('La session a changé. Reconnectez-vous.');
  let data;try{data=await response.json();}catch{throw Error('Le serveur n’a pas renvoyé une réponse valide.');}
  if(response.status===401&&auth===credentials){showLogin('Votre session a expiré. Reconnectez-vous.');}
  if(!response.ok)throw Error(data.error||'La demande a échoué.');return data;
}
function clearPrivateState(){
  clearTimeout(sessionTimer);sessionTimer=null;credentials=null;connectionEpoch++;pendingSends.clear();
  state={demo:false,chats:[],settings:{...initialSettings},connected:false,usage:0};
  selected=null;threadMessages=[];lastMessages='';$('reply').value='';$('password').value='';
  $('password').type='password';$('password-toggle').textContent='Afficher';$('password-toggle').setAttribute('aria-pressed','false');$('password-toggle').setAttribute('aria-label','Afficher le mot de passe');
  $('search').value='';$('save-status').textContent='';filter='all';loadSettings();render();
}
function showLogin(message=''){
  clearPrivateState();$('panel-shell').hidden=true;$('login-screen').hidden=false;$('connect-error').textContent=message;document.title='Relais · Connexion';
}
function openDemo(){
  clearPrivateState();state={demo:true,chats:structuredClone(demoChats),settings:{...initialSettings},connected:false,usage:0};selected=1;threadMessages=state.chats[0].messages;
  $('login-screen').hidden=true;$('panel-shell').hidden=false;document.title='Relais · Démonstration';setView('inbox');render();
}
async function logout(){
  if(state.demo){showLogin();return;}
  let message='Vous êtes déconnectée. Le bot continue de fonctionner sur le serveur.';
  try{if(credentials)await request('/api/logout','POST',{});}catch{message='Accès fermé dans cet onglet. Serveur injoignable : la session distante expirera automatiquement.';}
  finally{showLogin();toast(message);}
}
async function busy(button,action){button.disabled=true;try{await action();}catch(error){toast(error.message||'Connexion impossible.');}finally{button.disabled=false;}}
function renderList(){const query=$('search').value.trim().toLocaleLowerCase();const chats=state.chats.filter(c=>(filter!=='unread'||c.unread>0)&&(filter!=='manual'||c.mode==='manual')&&c.name.toLocaleLowerCase().includes(query));$('chat-count').textContent=state.chats.length;$('nav-count').textContent=state.chats.length;$('chat-list').innerHTML=chats.map(c=>`<button class="chat-item ${c.id===selected?'selected':''}" data-chat="${c.id}" aria-pressed="${c.id===selected}"><span class="avatar">${esc(c.name.slice(0,1))}</span><span class="chat-meta"><span class="chat-title"><strong>${esc(c.name)}</strong><time>${time(c.updated)}</time></span><span class="chat-preview">${esc(c.preview||'Nouvelle conversation')}</span><span class="chat-tag ${c.mode==='manual'?'manual':''}">${c.mode==='manual'?'↔ Vous avez la main':'✧ Assistant IA'}</span>${c.unread?`<span class="unread">${c.unread}</span>`:''}</span></button>`).join('')||'<div class="empty">Aucune conversation ici pour le moment.</div>';}
function renderThread(){const c=state.chats.find(c=>c.id===selected);const manual=c?.mode==='manual';$('thread-name').textContent=c?.name||'Votre boîte de réception';$('thread-avatar').textContent=c?.name.slice(0,1)||'T';$('thread-subtitle').textContent=state.demo?'Conversation fictive · Démonstration':'Conversation privée · Telegram';const label=manual?'Réactiver l’IA':'Prendre la main';$('takeover').textContent=label;$('side-toggle').textContent=label;for(const id of ['takeover','side-toggle','send','draft'])$(id).disabled=!c||(!state.demo&&!state.connected);$('mode-strip').classList.toggle('manual',manual||!state.settings.enabled);$('mode-strip').textContent=!state.settings.enabled?'Ⅱ L’IA est en pause pour tous les clients.':manual?'↔ Vous avez la main. L’IA attend votre réactivation.':'✧ L’IA peut répondre · Vous pouvez intervenir à tout moment';$('side-mode').textContent=manual?'Vous avez la main':'L’IA s’occupe des réponses';$('side-explain').textContent=manual?'L’IA ne répond plus à ce client. Réactivez-la quand vous avez terminé.':'Elle suit vos consignes et répond aux nouveaux messages de ce client.';const signature=JSON.stringify([selected,threadMessages]);if(signature!==lastMessages){const nearBottom=$('messages').scrollHeight-$('messages').scrollTop-$('messages').clientHeight<80;const changed=lastMessages==='' ;lastMessages=signature;$('messages').innerHTML=threadMessages.length?'<div class="day-label">'+(state.demo?'Exemple de conversation':'Messages synchronisés')+'</div>'+threadMessages.map(m=>`<article class="message ${m.source==='client'?'':'out'}"><div class="bubble">${esc(m.text)}</div><div class="message-meta"><b>${m.source==='ai'?'✧ Assistant IA':m.source==='client'?esc(c?.name||'Client'):'Vous'}</b><time>${new Date(m.created*1000).toLocaleDateString('fr-FR',{day:'2-digit',month:'2-digit'})} · ${time(m.created)}</time></div></article>`).join(''):'<div class="empty">Les nouveaux messages de vos clients apparaîtront ici une fois Telegram connecté.</div>';if(nearBottom||changed)$('messages').scrollTop=$('messages').scrollHeight;}}
function render(){renderList();renderThread();$('demo-banner').hidden=!state.demo;$('global-pause').textContent=state.settings.enabled?'Mettre toute l’IA en pause':'Réactiver toute l’IA';$('connection-status').textContent=state.demo?'Démonstration':state.connected?'Telegram connecté':'Telegram déconnecté';$('connection-status').classList.toggle('muted',state.demo||!state.connected);$('account-status').textContent=state.demo?'Non connecté':state.connected?'Connecté au serveur':'Connexion interrompue';$('list-footer').textContent=state.demo?'Données fictives · rien n’est envoyé':`${state.usage||0} / ${state.settings.daily_limit} demandes IA aujourd’hui${state.last_error?' · '+state.last_error:''}`;$('connect-open').title=state.demo?'Se connecter':'Se déconnecter';$('logout').textContent=state.demo?'Fermer la démo':'Se déconnecter';}
function loadSettings(){for(const key of ['tone','catalog','faq'])$(key).value=state.settings[key];$('enabled').checked=state.settings.enabled;$('daily-limit').value=state.settings.daily_limit;}
function renderGlossary(){$('glossary-rows').innerHTML=glossaryRows.map((entry,i)=>`<div class="glossary-row" data-index="${i}"><input type="text" class="glossary-expression" placeholder="Ce que le client écrit (ex : dispo)" maxlength="100" value="${esc(entry.expression)}"><span class="glossary-arrow" aria-hidden="true">→</span><input type="text" class="glossary-replacement" placeholder="Ce que ça veut dire (ex : disponibilité)" maxlength="200" value="${esc(entry.replacement)}"><button type="button" class="glossary-remove" aria-label="Supprimer cette ligne">✕</button></div>`).join('')||'<p class="field-help">Aucune expression enregistrée pour l’instant.</p>';}
function loadGlossary(){glossaryRows=(state.settings.glossary||[]).map(e=>({expression:e.expression||'',replacement:e.replacement||''}));if(!glossaryRows.length)glossaryRows=[{expression:'',replacement:''}];renderGlossary();}
function setView(next){view=next;$('inbox-view').hidden=next!=='inbox';$('settings-view').hidden=next!=='settings';$('glossary-view').hidden=next!=='glossary';$('page-title').textContent=next==='inbox'?'Conversations':next==='settings'?'Mon assistant':'Glossaire';document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===next));if(next==='settings')loadSettings();if(next==='glossary')loadGlossary();}
async function selectChat(id){selected=id;lastMessages='';$('reply').value='';if(state.demo){const c=state.chats.find(c=>c.id===id);threadMessages=c.messages;c.unread=0;}else{const data=await request(`/api/chats/${id}/messages`);if(selected!==id)return;threadMessages=data.messages;await request(`/api/chats/${id}/read`,'POST',{});const c=state.chats.find(c=>c.id===id);if(c)c.unread=0;}render();}
async function refresh(){if(state.demo||!credentials||refreshing)return;refreshing=true;const epoch=connectionEpoch;try{const fresh=await request('/api/state');if(epoch!==connectionEpoch)return;state={...fresh,demo:false};if(!state.chats.some(c=>c.id===selected)){selected=state.chats[0]?.id??null;lastMessages='';}if(selected){const id=selected;const data=await request(`/api/chats/${id}/messages`);if(epoch!==connectionEpoch)return;if(id===selected)threadMessages=data.messages;}else threadMessages=[];render();}catch(error){if(epoch!==connectionEpoch)return;state.connected=false;render();$('connection-status').textContent='Serveur injoignable';}finally{refreshing=false;}}
async function setMode(mode){const id=selected;const c=state.chats.find(c=>c.id===id);if(!c)throw Error('Sélectionnez une conversation.');if(!state.demo)await request(`/api/chats/${id}/mode`,'POST',{mode});const current=state.chats.find(item=>item.id===id);if(current)current.mode=mode;render();toast(mode==='manual'?'Vous avez la main. L’IA est en pause pour ce client.':'L’IA répondra aux prochains messages.');return {id,mode};}
async function saveSettings(settings){if(!state.demo)await request('/api/settings','POST',settings);state.settings=settings;render();}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
$('search').addEventListener('input',renderList);
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));renderList();}));
$('chat-list').addEventListener('click',event=>{const b=event.target.closest('[data-chat]');if(b)busy(b,()=>selectChat(Number(b.dataset.chat)));});
for(const id of ['takeover','side-toggle'])$(id).addEventListener('click',()=>busy($(id),()=>setMode(state.chats.find(c=>c.id===selected)?.mode==='manual'?'auto':'manual')));
$('global-pause').addEventListener('click',()=>busy($('global-pause'),async()=>{await saveSettings({...state.settings,enabled:!state.settings.enabled});toast(state.settings.enabled?'IA réactivée. Les conversations manuelles restent en pause.':'Toutes les réponses automatiques sont en pause.');}));
$('send').addEventListener('click',()=>busy($('send'),async()=>{const text=$('reply').value.trim();if(!text)return;const id=selected;if(state.demo){const c=state.chats.find(c=>c.id===id);c.mode='manual';c.messages.push({id:Date.now(),source:'human',text,created:Date.now()/1000});c.preview=text;c.updated=Date.now()/1000;threadMessages=c.messages;}else{const prior=pendingSends.get(id);const pending=prior?.text===text?prior:{text,request_id:crypto.randomUUID()};pendingSends.set(id,pending);await request(`/api/chats/${id}/reply`,'POST',pending);pendingSends.delete(id);await refresh();}if(selected===id)$('reply').value='';render();toast(state.demo?'Réponse ajoutée à la démonstration.':'Message envoyé. Vous avez la main.');}));
$('draft').addEventListener('click',()=>busy($('draft'),async()=>{const id=selected;const prior=$('reply').value;const result=state.demo?{text:'Merci pour ton message ! Je regarde ta demande et je reviens vers toi avec les détails.'}:await request(`/api/chats/${id}/draft`,'POST',{});if(id!==selected||$('reply').value!==prior){toast('La conversation a changé : proposition non insérée.');return;}$('reply').value=result.text;toast(state.demo?'Exemple de réponse : vous pouvez le modifier avant l’envoi.':'Proposition prête. Relisez-la avant de l’envoyer.');}));
$('settings-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{const settings={tone:$('tone').value.trim(),catalog:$('catalog').value.trim(),faq:$('faq').value.trim(),enabled:$('enabled').checked,daily_limit:Number($('daily-limit').value),glossary:state.settings.glossary||[]};await saveSettings(settings);$('save-status').textContent=state.demo?'Enregistré pour cette démonstration.':'Consignes enregistrées.';toast(state.demo?'Consignes mises à jour dans la démo.':'Consignes enregistrées sur le serveur.');});});
$('glossary-add').addEventListener('click',()=>{glossaryRows.push({expression:'',replacement:''});renderGlossary();});
$('glossary-rows').addEventListener('click',event=>{const row=event.target.closest('.glossary-row');if(!row||!event.target.closest('.glossary-remove'))return;glossaryRows.splice(Number(row.dataset.index),1);renderGlossary();});
$('glossary-rows').addEventListener('input',event=>{const row=event.target.closest('.glossary-row');if(!row)return;const entry=glossaryRows[Number(row.dataset.index)];if(!entry)return;if(event.target.classList.contains('glossary-expression'))entry.expression=event.target.value;if(event.target.classList.contains('glossary-replacement'))entry.replacement=event.target.value;});
$('glossary-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{const glossary=glossaryRows.map(e=>({expression:e.expression.trim(),replacement:e.replacement.trim()})).filter(e=>e.expression);await saveSettings({...state.settings,glossary});loadGlossary();$('glossary-save-status').textContent=state.demo?'Enregistré pour cette démonstration.':'Glossaire enregistré.';toast(state.demo?'Glossaire mis à jour dans la démo.':'Glossaire enregistré sur le serveur.');});});
$('connect-open').addEventListener('click',()=>state.demo?showLogin():busy($('connect-open'),logout));
$('connect-banner').addEventListener('click',()=>showLogin());
$('logout').addEventListener('click',()=>busy($('logout'),logout));
$('demo-open').addEventListener('click',openDemo);
$('password-toggle').addEventListener('click',()=>{const visible=$('password').type==='password';$('password').type=visible?'text':'password';$('password-toggle').textContent=visible?'Masquer':'Afficher';$('password-toggle').setAttribute('aria-pressed',String(visible));$('password-toggle').setAttribute('aria-label',visible?'Masquer le mot de passe':'Afficher le mot de passe');});
$('connect-form').addEventListener('submit',event=>{
  event.preventDefault();busy(event.submitter,async()=>{
    $('connect-error').textContent='';
    const attemptEpoch=connectionEpoch;
    try{
      const url=new URL($('server-url').value);
      if(url.protocol!=='https:'&&!(['localhost','127.0.0.1'].includes(url.hostname)&&url.protocol==='http:'))throw Error('Utilisez une adresse HTTPS.');
      if(url.username||url.password||url.search||url.hash||url.pathname!=='/')throw Error('Indiquez uniquement l’adresse du serveur, sans chemin.');
      const response=await fetch(url.origin+'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('username').value.trim(),password:$('password').value}),credentials:'omit',redirect:'error',signal:AbortSignal.timeout(20000)});
      let session;try{session=await response.json();}catch{throw Error('Adresse du serveur invalide ou serveur indisponible.');}
      if(attemptEpoch!==connectionEpoch)throw Error('Connexion interrompue.');
      if(!response.ok)throw Error(session.error||'Connexion refusée.');
      if(typeof session.token!=='string'||!Number.isFinite(session.expires_in)||session.expires_in<=0)throw Error('Réponse de connexion invalide.');
      const auth={url:url.origin,key:session.token};const data=await request('/api/state','GET',undefined,auth);
      if(!Array.isArray(data.chats)||!data.settings)throw Error('Cette adresse ne correspond pas au serveur Relais.');
      clearPrivateState();credentials=auth;state={...data,demo:false};selected=state.chats[0]?.id??null;
      try{localStorage.setItem('relais-server-url',url.origin);}catch{}
      $('server-details').open=false;$('login-screen').hidden=true;$('panel-shell').hidden=false;document.title='Relais · Conversations';
      sessionTimer=setTimeout(()=>showLogin('Votre session a expiré. Reconnectez-vous.'),Math.min(session.expires_in,28800)*1000);
      setView('inbox');loadSettings();render();await refresh();toast('Bienvenue dans votre espace privé.');
    }catch(error){$('connect-error').textContent=error.message||'Connexion impossible.';}
    finally{$('password').value='';}
  });
});
try{$('server-url').value=localStorage.getItem('relais-server-url')||'';}catch{}
$('server-details').open=!$('server-url').value;
showLogin();setInterval(refresh,3000);

// Optional structured controls share exactly the same application actions.
if (document.modelContext?.registerTool) {
  const lifecycle = new AbortController();
  const register = tool => Promise.resolve(document.modelContext.registerTool(tool, {signal:lifecycle.signal})).catch(() => {});
  register({name:'list_conversations',description:'List the clients and current assistant modes in this connected panel or demonstration.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:async input=>{if(!input||typeof input!=='object'||Object.keys(input).length)throw Error('No parameters expected.');return {demo:state.demo,conversations:state.chats.map(({id,name,mode})=>({id,name,mode}))};}});
  register({name:'pause_conversation',description:'Select a conversation and pause its automatic AI replies. Does not send a message.',inputSchema:{type:'object',properties:{id:{type:'integer'}},required:['id'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},execute:async input=>{if(!input||Object.keys(input).length!==1||!Number.isInteger(input.id)||!state.chats.some(c=>c.id===input.id))throw Error('Unknown conversation.');setView('inbox');await selectChat(input.id);return await setMode('manual');}});
  window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
}
