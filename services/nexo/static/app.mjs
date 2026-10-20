import {AGENTS,agentName,STATUS,eventStory,agentStates,milestones,activityLine} from './model.mjs';
const $=id=>document.getElementById(id);
const money=n=>new Intl.NumberFormat('es-CO',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(n));
const storage={get(k,fallback){try{return JSON.parse(localStorage.getItem(k))??fallback}catch{return fallback}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch{}}};
let catalog=[],cart={},selected='',fetching=false,catalogLoading=false,buying=false,pending=null,history=storage.get('nexo-orders',[]);
// Activity rows already on screen: only new ones animate when the panel refreshes.
let seenActivity=new Set();
if(!Array.isArray(history))history=[];
const productInfo={
 'LAPTOP-PRO':{name:'Laptop Pro 14',category:'Potencia portátil',color:'#efedf5',kind:'laptop'},
 'MONITOR-27':{name:'Monitor 4K de 27 pulgadas',category:'Un espacio para crear',color:'#e8eeec',kind:'monitor'},
 'DOCK-USB-C':{name:'Estación USB-C',category:'Todo conectado',color:'#f4ebe3',kind:'dock'},
 'KEYBOARD-MX':{name:'Teclado mecánico',category:'Precisión en cada tecla',color:'#eceaf1',kind:'keyboard'},
 'HEADSET-PRO':{name:'Audífonos con cancelación',category:'Enfócate en lo que importa',color:'#e8edf3',kind:'headset'},
 'WEBCAM-4K':{name:'Cámara web 4K',category:'Hazte presente',color:'#f0e8e9',kind:'webcam'}
};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function notice(text){$('message').textContent=text;$('message').hidden=!text}
function detail(title,value,key){const d=node('details');if(key)d.dataset.key=key;d.append(node('summary',title),node('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));return d}
async function api(path,options){const r=await fetch(path,options);const d=await r.json();if(!r.ok)throw Error(d.error||'No fue posible completar la solicitud.');return d}
function view(which){$('shop-view').hidden=which!=='shop';$('trace-view').hidden=which!=='trace';$('shop-tab').classList.toggle('active',which==='shop');$('trace-tab').classList.toggle('active',which==='trace');$('shop-tab').setAttribute('aria-pressed',which==='shop');$('trace-tab').setAttribute('aria-pressed',which==='trace')}
$('brand-home').onclick=e=>{e.preventDefault();view('shop');window.scrollTo({top:0,behavior:'smooth'})};$('shop-tab').onclick=()=>view('shop');$('trace-tab').onclick=()=>view('trace');$('back-shop').onclick=()=>view('shop');
function art(kind){
 const drawings={
 laptop:'<rect x="48" y="20" width="144" height="94" rx="7" fill="#4b425b"/><rect x="56" y="28" width="128" height="76" rx="2" fill="#b6a6d0"/><path d="M56 104L116 28H184V104Z" fill="#d5c9e5"/><path d="M48 114H192L215 132Q215 138 205 138H35Q25 138 25 132Z" fill="#bbb7c4"/><path d="M97 114H143L150 122H90Z" fill="#96919f"/>',
 monitor:'<rect x="30" y="15" width="180" height="107" rx="7" fill="#414c4d"/><rect x="38" y="23" width="164" height="89" rx="2" fill="#a1b7b0"/><path d="M38 112L130 23H202V112Z" fill="#c7d7c9"/><path d="M111 122H129V140H151V146H89V140H111Z" fill="#777f7d"/>',
 dock:'<path d="M49 59L171 39L207 67L85 90Z" fill="#c7b9aa"/><path d="M49 59L85 90V113L49 83Z" fill="#8c7e71"/><path d="M85 90L207 67V91L85 113Z" fill="#a99c90"/><path d="M100 95L119 91M131 89L150 85M163 83L186 79" stroke="#48434a" stroke-width="6"/><path d="M51 65Q10 27 24 18" fill="none" stroke="#5e5660" stroke-width="5"/>',
 keyboard:'<path d="M34 59L176 36L215 101L73 125Z" fill="#686073"/><path d="M34 59L73 125V134L34 68Z" fill="#50495d"/><path d="M73 125L215 101V110L73 134Z" fill="#92899d"/>'+Array.from({length:40},(_,i)=>{const row=Math.floor(i/10),col=i%10;return `<rect x="${48+col*13+row*7}" y="${62+row*13-col*2}" width="10" height="9" rx="2" fill="${i%7===0?'#c8b3d9':'#e2dce8'}"/>`}).join(''),
 headset:'<path d="M65 101V70A55 55 0 01175 70V101" fill="none" stroke="#4d5364" stroke-width="17"/><path d="M66 67A54 54 0 01174 67" fill="none" stroke="#8791a5" stroke-width="9"/><rect x="49" y="77" width="33" height="57" rx="14" fill="#414757"/><rect x="158" y="77" width="33" height="57" rx="14" fill="#414757"/><rect x="72" y="83" width="15" height="47" rx="7" fill="#aeb9ca"/><rect x="153" y="83" width="15" height="47" rx="7" fill="#aeb9ca"/>',
 webcam:'<path d="M110 90H131V118L158 132H84L110 118Z" fill="#8b808c"/><rect x="58" y="40" width="125" height="58" rx="22" fill="#49424f"/><circle cx="120" cy="68" r="23" fill="#72617b"/><circle cx="120" cy="68" r="15" fill="#2c283b"/><circle cx="120" cy="68" r="9" fill="#605b88"/><circle cx="114" cy="62" r="4" fill="#a4a6d3"/><circle cx="164" cy="66" r="3" fill="#a2c2ab"/>'};
 return '<svg viewBox="0 0 240 160" aria-hidden="true">'+drawings[kind]+'</svg>';
}
function renderCatalog(){
 $('products').replaceChildren(...catalog.map(p=>{const info=productInfo[p.productId];const c=node('article',undefined,'product'),a=node('div',undefined,'product-art');a.style.background=info.color;a.innerHTML=art(info.kind);a.append(node('span',Number(p.available)>0?`${p.available} disponibles`:'Sin existencias','stock'));const body=node('div',undefined,'product-body'),price=node('div',money(p.unitPrice)+' ','price');price.append(node('small','USD'));const add=node('button','Agregar al carrito +');add.setAttribute('aria-label','Agregar '+info.name);add.onclick=()=>changeQty(p.productId,1);body.append(node('span',info.category,'category'),node('h3',info.name),price,add);c.append(a,body);return c}));
}
async function loadCatalog(){if(catalogLoading)return;catalogLoading=true;$('reload-catalog').disabled=true;try{const d=await api('/api/catalog');catalog=d.products;const current=$('customer').value;$('customer').replaceChildren(...d.customers.map(c=>{const o=node('option',c.name);o.value=c.id;return o}));if(d.customers.some(c=>c.id===current))$('customer').value=current;renderCatalog();renderCart();}catch(e){notice('No pudimos cargar el catálogo. '+e.message);if(!catalog.length)$('products').replaceChildren(node('p','Inicia el stack local y pulsa «Actualizar stock».'))}finally{catalogLoading=false;$('reload-catalog').disabled=false}}
function resetRequest(){pending=null;storage.set('nexo-pending',null)}
function changeQty(id,delta){if(buying)return;cart[id]=Math.min(100,Math.max(0,(cart[id]||0)+delta));if(!cart[id])delete cart[id];resetRequest();renderCart()}
function renderCart(){
 const lines=Object.entries(cart).filter(([id])=>catalog.some(p=>p.productId===id));const sum=lines.reduce((s,[id,q])=>s+Number(catalog.find(p=>p.productId===id).unitPrice)*q,0),discount=Number($('discount').value)||0;
 $('cart-lines').replaceChildren(...lines.map(([id,q])=>{const item=node('div',undefined,'cart-line'),row=node('div',undefined,'row'),qty=node('div',undefined,'quantity');for(const [label,delta] of [['−',-1],['+',1]]){const b=node('button',label);b.setAttribute('aria-label',(delta<0?'Quitar una unidad de ':'Agregar una unidad de ')+productInfo[id].name);b.onclick=()=>changeQty(id,delta);if(delta===1)qty.append(node('span',q));qty.append(b)}row.append(qty,node('small',money(Number(catalog.find(p=>p.productId===id).unitPrice)*q)));item.append(node('strong',productInfo[id].name),row);return item}));
 if(!lines.length)$('cart-lines').append(node('p','Tu carrito está vacío. Agrega un producto para empezar.','hint'));
 $('cart-count').textContent=lines.reduce((s,[,q])=>s+q,0)+' unidades';$('subtotal').textContent=money(sum);$('discount-total').textContent='− '+money(sum*discount/100);$('total').textContent=money(sum*(100-discount)/100);$('checkout').disabled=buying||!lines.length||!Number.isFinite(discount)||discount<0||discount>100;
 $('discount-hint').textContent=discount>10?'Esta solicitud supera el 10%: requiere una decisión del CEO.':'El gerente comercial puede autorizar hasta el 10%.';
}
function preset(kind){
 if(buying)return;
 const wanted=kind==='stock'?{'DOCK-USB-C':3}:kind==='duplicate'?{'MONITOR-27':2}:{'LAPTOP-PRO':3};cart=wanted;$('discount').value=kind==='discount'?25:0;$('duplicate').checked=kind==='duplicate';resetRequest();renderCart();document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('selected',b.dataset.preset===kind));notice(kind==='stock'?'La demo solicita 3 estaciones USB-C. Si el inventario cambió, verifica el stock disponible antes de presentar.':'');
}
$('presets').onclick=e=>{const b=e.target.closest('[data-preset]');if(b)preset(b.dataset.preset)};
$('discount').oninput=()=>{resetRequest();renderCart()};$('customer').onchange=resetRequest;$('duplicate').onchange=resetRequest;$('reload-catalog').onclick=loadCatalog;
function renderHistory(){
 $('order-count').textContent=history.length;$('history').replaceChildren(node('option','Selecciona un pedido'),...history.map(o=>{const n=node('option',o.label+' · '+o.id);n.value=o.id;return n}));$('history').firstChild.value='';if(history.some(o=>o.id===selected))$('history').value=selected;
}
function remember(id,label){history=[{id,label},...history.filter(o=>o.id!==id)].slice(0,30);storage.set('nexo-orders',history);renderHistory()}
async function buy(){
 if(buying)return;
 const lines=Object.entries(cart).map(([productId,quantity])=>({productId,quantity}));if(!lines.length)return;
 const fingerprint=JSON.stringify({lines,customerId:$('customer').value,discount:$('discount').value});
 const saved=storage.get('nexo-pending',null);if(!pending&&saved?.fingerprint===fingerprint)pending=saved;
 if(!pending||pending.fingerprint!==fingerprint)pending={fingerprint,body:{requestId:crypto.randomUUID(),customerId:$('customer').value,lines,discount:$('discount').value}};
 storage.set('nexo-pending',pending);const submitted=pending;const requestBody=JSON.stringify(submitted.body),twice=$('duplicate').checked;buying=true;
 for(const id of ['discount','customer','duplicate'])$(id).disabled=true;
 $('checkout').disabled=true;$('checkout').textContent='Enviando pedido…';notice('');
 try{const d=await api('/api/checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:requestBody});
 if(twice)await api('/api/checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:requestBody});
 remember(d.orders[0],twice?'Compra duplicada':Number(submitted.body.discount)>10?'Descuento para el CEO':'Compra en Nexo');resetRequest();selectOrder(d.orders[0]);view('trace');window.scrollTo({top:0,behavior:'smooth'});if(twice)notice('Enviamos dos veces el mismo pedido. La empresa debe conservar una sola orden y una sola operación de cada tipo.');
 }catch(e){notice('No se pudo confirmar la compra. '+e.message+' Puedes reintentar: conservaremos el mismo identificador.')}finally{buying=false;for(const id of ['discount','customer','duplicate'])$(id).disabled=false;$('checkout').textContent='Confirmar compra →';renderCart()}
}
$('checkout').onclick=buy;
function selectOrder(id){selected=id.trim();$('order-input').value=selected;$('order-title').textContent=selected;$('trace-empty').hidden=!!selected;$('trace-content').hidden=!selected;$('sessions').replaceChildren();for(const k of ['timeline','records','activity','ceo-card','departments','milestones','order-facts'])$(k).replaceChildren();$('activity-count').textContent='0 acciones';seenActivity=new Set();$('order-status').textContent='Esperando datos';$('updated').textContent='';renderHistory();refresh()}
$('history').onchange=e=>selectOrder(e.target.value);$('order-form').onsubmit=e=>{e.preventDefault();notice('');const id=$('order-input').value.trim();if(id){remember(id,'Pedido consultado');selectOrder(id)}};$('refresh').onclick=()=>refresh();
function agentCard(a){const n=node('article',undefined,'agent'),top=node('div',undefined,'agent-top'),names=node('div');names.append(node('strong',a.name),node('small',a.role));top.append(node('span',a.icon,'avatar'),names);n.append(top,node('div',a.state,'agent-status '+a.tone));if(a.summary)n.append(detail('Último resultado registrado',a.summary,'agent-'+a.id));return n}
function renderTrace(data){
 const expanded=new Set([...document.querySelectorAll('details[open][data-key]')].map(d=>d.dataset.key));
 const order=data.records.find(r=>r.orderId===selected&&r.sk==='META');$('order-status').textContent=order?(STATUS[order.status]||order.status):'Esperando eventos';
 $('order-facts').replaceChildren();if(order){const facts=$('order-facts');facts.append(node('div','Cliente: '+order.customerId),node('strong','Total del pedido: '+money(order.total)),node('div','Descuento aplicado: '+order.discountPercent+'%'));}
 $('milestones').replaceChildren(...milestones(data).map(m=>{const n=node('div',undefined,'milestone '+m.state);n.append(node('span',m.state==='done'?'✓':m.state==='error'?'!':'○'),node('strong',m.name));return n}));
 const agents=agentStates(data);$('ceo-card').replaceChildren(agentCard(agents[0]));$('departments').replaceChildren(...['Comercial','Operaciones','Contabilidad'].map(name=>{const d=node('div',undefined,'department');d.append(node('h3',name),...agents.filter(a=>a.department===name).map(agentCard));return d}));
 $('event-count').textContent=data.events.length+' eventos';$('timeline').replaceChildren(...data.events.map(e=>{const [title,text]=eventStory(e),n=node('article',undefined,'event '+(e.eventType.startsWith('Decision')?'decision':'')+(/Failed|Rejected|Unavailable/.test(e.eventType)?' error':''));n.append(node('span',new Date(e.timestamp).toLocaleTimeString('es-CO')+' · '+agentName(e.source),'event-meta'),node('h3',title),node('p',text),detail('Ver evento y correlación',e,'event-'+e.eventId));return n}));if(!data.events.length)$('timeline').append(node('p','Esperando el primer evento de este pedido…','hint'));
 const labels={META:'Pedido',PAYMENT:'Pago',INVOICE:'Factura',SHIPMENT:'Envío',RESERVATION:'Reserva'};
 $('records').replaceChildren(...data.records.map(r=>{const d=node('div',undefined,'record');d.append(node('span',STATUS[r.status]||r.status||'Registrado','pill'));d.append(node('strong',r.taskId?'Tarea delegada':r.decisionId?'Decisión del CEO':labels[r.sk]||r.sk));if(r.assignedTo)d.append(node('small',agentName(r.assignedBy)+' → '+agentName(r.assignedTo)));if(r.decisionId)d.append(node('small','Solicita: '+agentName(r.requestedBy)));if(r.goal)d.append(node('small',r.goal));if(r.reason)d.append(node('small',r.reason));d.append(detail('Inspeccionar registro',r,'record-'+r.pk+'-'+r.sk));return d}));
 const lines=(data.activity?.timeline||[]).map(e=>({e,line:activityLine(e)})).filter(x=>x.line).reverse().slice(0,80);
 $('activity-count').textContent=lines.length+' acciones';
 const firstPaint=!seenActivity.size;
 $('activity').replaceChildren(...lines.map(({e,line})=>{const key=e.at+e.agentId+e.kind+(e.tool||e.to||''),fresh=!firstPaint&&!seenActivity.has(key);seenActivity.add(key);const li=node('li',undefined,'activity-row '+line.tone+(fresh?' fresh':''));li.append(node('time',new Date(e.at).toLocaleTimeString('es-CO',{hour12:false})),node('span',line.icon,'activity-icon'),node('strong',line.who),node('span',line.text,'activity-text'));return li}));
 if(!lines.length)$('activity').append(node('li','Cuando un agente trabaje en este pedido, lo verás aquí.','hint'));
 for(const d of document.querySelectorAll('details[data-key]'))d.open=expanded.has(d.dataset.key);
 $('updated').textContent='Actualizado '+new Date().toLocaleTimeString('es-CO');
}
async function refresh(){if(!selected||fetching)return;fetching=true;const id=selected;try{const d=await api('/api/trace?order='+encodeURIComponent(id));if(selected!==id)return;renderTrace(d);if(d.warnings.length)notice('Algunos registros no están disponibles: '+d.warnings.join('\n'));}catch(e){if(selected===id)notice('No pudimos actualizar el pedido. '+e.message)}finally{fetching=false}}
$('load-transcripts').onclick=async()=>{if(!selected)return;const id=selected;$('load-transcripts').disabled=true;$('sessions').textContent='Consultando las sesiones de los siete agentes…';try{const rows=await api('/api/transcripts?order='+encodeURIComponent(id));if(selected!==id)return;$('sessions').replaceChildren(...rows.map(a=>{const d=node('div',undefined,'session-group');d.append(node('h3',agentName(a.agent)));if(a.error)d.append(node('p','No disponible: '+a.error));else if(!a.sessions.length)d.append(node('p','Todavía no hay conversaciones de este agente para el pedido.'));for(const s of a.sessions)d.append(detail(s.session,s.text));return d}));}catch(e){if(selected===id)$('sessions').textContent='No pudimos cargar las conversaciones. '+e.message}finally{$('load-transcripts').disabled=false}};
setInterval(()=>{if($('auto').checked&&!$('trace-view').hidden)refresh()},2000);
renderHistory();loadCatalog().then(()=>{
 const saved=storage.get('nexo-pending',null);
 if(saved?.body?.lines&&saved.fingerprint){
  pending=saved;cart=Object.fromEntries(saved.body.lines.map(l=>[l.productId,l.quantity]));
  $('customer').value=saved.body.customerId;$('discount').value=saved.body.discount;renderCart();
  notice('Hay una compra sin confirmación. Al reintentar usaremos el mismo identificador.');
 }else if(catalog.length)preset('normal');
});
