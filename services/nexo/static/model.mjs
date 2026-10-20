export const AGENTS = [
 {id:'ceo',name:'Dirección general',role:'CEO',department:'Dirección',icon:'✦'},
 {id:'commercial-manager',name:'Gerente comercial',role:'Gerente',department:'Comercial',icon:'C'},
 {id:'sales-rep',name:'Asesor de ventas',role:'Especialista',department:'Comercial',icon:'V'},
 {id:'operations-manager',name:'Gerente de operaciones',role:'Gerente',department:'Operaciones',icon:'O'},
 {id:'fulfillment',name:'Especialista de despachos',role:'Especialista',department:'Operaciones',icon:'D'},
 {id:'accounting-manager',name:'Gerente de contabilidad',role:'Gerente',department:'Contabilidad',icon:'A'},
 {id:'accountant',name:'Contador',role:'Especialista',department:'Contabilidad',icon:'$'}
];
export const agentName=id=>AGENTS.find(a=>a.id===id)?.name||({'order-simulator':'Tienda / simulador'}[id])||id||'Sistema';
export const STATUS={PLACED:'Recibido',VALIDATED:'Validado',REJECTED:'Rechazado',PENDING:'Pendiente',ASSIGNED:'Asignado',IN_PROGRESS:'En proceso',COMPLETED:'Completado',FAILED:'Fallido',ESCALATED:'Escalado',APPROVED:'Aprobado',RECEIVED:'Recibido',CREATED:'Creado',DISPATCHED:'Despachado',DELIVERED:'Entregado',ISSUED:'Emitida',RESERVED:'Reservado',RELEASED:'Liberado',CANCELLED:'Cancelado',REFUNDED:'Reembolsado',VOID:'Anulada'};
export function eventStory(e){
 const p=e.payload||{},who=agentName(e.source),to=agentName(p.assignedTo),reason=p.reason||p.message||'';
 const stories={
 OrderPlaced:['Pedido recibido','La tienda entregó el pedido a la empresa. Los agentes comenzarán a procesarlo.'],
 OrderValidated:['Condiciones comerciales validadas',`${who} validó el pedido. Operaciones y Contabilidad pueden trabajar en paralelo.`],
 WorkAssigned:['Trabajo delegado',`${agentName(p.assignedBy||e.source)} → ${to}. ${p.goal||'Se creó una tarea duradera para este pedido.'}`],
 WorkCompleted:['Tarea completada',`${agentName(p.agentId||e.source)} reportó el resultado de su tarea. ${p.result||''}`],
 WorkFailed:['La tarea encontró un problema',`${agentName(p.agentId||e.source)}: ${p.errorCode||'error'}. ${reason}`],
 DecisionRequested:['Se requiere una decisión del CEO',`${who} escaló la solicitud fuera de su autoridad.${p.requestedValue!==undefined?' Valor solicitado: '+p.requestedValue+'.':''}`],
 DecisionApproved:['El CEO aprobó la solicitud',reason||'La aprobación quedó registrada y los agentes pueden continuar según sus procedimientos.'],
 DecisionRejected:['El CEO rechazó la solicitud',reason||'La decisión quedó registrada. Comercial debe aplicar sus procedimientos para continuar.'],
 InventoryReserved:['Inventario reservado','Operaciones reservó las unidades del pedido.'],
 InventoryUnavailable:['No hay inventario suficiente','Operaciones detectó faltantes. Consulta el detalle del evento para ver los productos afectados.'],
 PaymentReceived:['Pago conciliado','Contabilidad registró el pago recibido.'],
 PaymentRejected:['Pago rechazado',reason||'Contabilidad detectó que el pago no cumple las condiciones del pedido.'],
 InvoiceCreated:['Factura creada','Contabilidad generó la factura del pedido.'],
 ShipmentRequested:['Despacho solicitado','El pedido está pendiente de preparación logística.'],
 ShipmentCreated:['Envío creado','Operaciones creó el registro de envío. Esto todavía no confirma que haya sido despachado.'],
 ShipmentDispatched:['Pedido despachado','Operaciones confirmó la salida del envío.'],
 ShipmentDelivered:['Pedido entregado','Se registró la entrega del pedido.'],
 RefundRequested:['Reembolso solicitado','La solicitud de reembolso está pendiente del procedimiento correspondiente.'],
 OrderCancelled:['Pedido cancelado',`${who} canceló el pedido: ${p.reason||'sin motivo registrado'}.`+(p.inventoryReleased?' El inventario reservado volvió a estar disponible.':'')+(p.invoiceVoided?' La factura quedó anulada.':'')+(p.refundDue?` Contabilidad debe reembolsar ${p.refundDue}.`:'')],
 PaymentRefunded:['Pago reembolsado',`${who} reembolsó ${p.amount||''} al cliente. ${p.reason||''}`.trim()]};
 return stories[e.eventType]||[e.eventType,'Evento registrado por '+who+'.'];
}
const TOOL_LABELS={get_order:'revisa el pedido',list_orders:'revisa los pedidos',get_customer:'revisa al cliente',get_inventory:'revisa el inventario',get_order_margin:'calcula el margen',get_invoice:'revisa la factura',track_shipment:'revisa el envío',get_task:'revisa la tarea',list_my_tasks:'revisa sus tareas',get_decision:'revisa la decisión',list_pending_decisions:'revisa decisiones pendientes',validate_order:'valida el pedido',get_order_status:'revisa el estado del pedido',cancel_order:'cancela el pedido',refund_payment:'reembolsa el pago',reserve_inventory:'reserva inventario',release_inventory:'libera inventario',create_shipment:'crea el envío',create_invoice:'emite la factura',register_payment:'registra el pago',assign_task:'delega una tarea',start_task:'inicia su tarea',complete_task:'completa su tarea',fail_task:'reporta un fallo',request_ceo_approval:'escala al CEO',approve_decision:'aprueba la decisión',reject_decision:'rechaza la decisión',whoami:'confirma su identidad'};
export const toolLabel=t=>TOOL_LABELS[t]||t||'trabaja';
// Live state from the agent-activity service first, durable records (tasks, decisions) second.
export function agentStates(data){
 const live=Object.fromEntries((data.activity?.agents||[]).map(a=>[a.id,a]));
 return AGENTS.map(a=>{
 const now=live[a.id]||{},tasks=data.records.filter(r=>r.assignedTo===a.id);
 const pendingDecision=a.id==='ceo'&&data.records.some(r=>r.decisionId&&r.status==='PENDING');
 let state='Sin actividad',tone='idle';
 if(now.involved||data.events.some(e=>e.source===a.id)){state='Participó';tone='done'}
 if(tasks.some(t=>t.status==='COMPLETED')){state='Tarea completada';tone='done'}
 if(tasks.some(t=>t.status==='FAILED')){state='Tarea con error';tone='error'}
 if(tasks.some(t=>['ASSIGNED','PENDING'].includes(t.status))||pendingDecision){state='Trabajo pendiente';tone='waiting'}
 if(tasks.some(t=>t.status==='IN_PROGRESS')){state='Trabajando';tone='working'}
 if(now.waitingFor){state='Esperando '+(now.waitingFor==='ceo'?'al CEO':'a '+agentName(now.waitingFor));tone='waiting'}
 if(now.onThisOrder){state=now.status==='working'?'Ahora: '+toolLabel(now.tool):'Analizando este pedido';tone='working'}
 else if(now.status==='error'&&now.involved){state='Reintentando';tone='error'}
 else if(now.busyElsewhere&&tone==='idle'){state='Ocupado con otro pedido';tone='idle'}
 return {...a,state,tone,summary:now.lastSummary||'',tasks};
 });
}
// One line of the live activity timeline, in plain Spanish.
export function activityLine(e){
 const who=agentName(e.agentId),to=agentName(e.to);
 switch(e.kind){
 case 'turn_started':return {who,icon:'▶',text:'toma el evento '+(e.event||''),tone:'start'};
 case 'tool_started':return {who,icon:'·',text:toolLabel(e.tool),tone:'tool'};
 case 'tool_finished':return e.ok===false?{who,icon:'✗',text:toolLabel(e.tool)+' no se pudo: '+(e.error||'error'),tone:'error'}:null;
 case 'message':return {who,icon:{delegate:'→',report:'↩',escalate:'⇑',decide:'⇓'}[e.messageKind]||'→',
   text:({delegate:'delega a ',report:'reporta a ',escalate:'escala a ',decide:'responde a '}[e.messageKind]||'escribe a ')+to+': '+(e.text||''),tone:e.messageKind==='escalate'?'escalate':e.messageKind==='decide'?'decide':'message'};
 case 'turn_finished':return {who,icon:'■',text:e.summary||'terminó su turno',tone:'done'};
 case 'turn_failed':return {who,icon:'!',text:'su turno falló (intento '+(e.attempt||'?')+'); se reintentará',tone:'error'};
 default:return null;
 }
}
export function milestones(data){
 const types=new Set(data.events.map(e=>e.eventType));
 const closing=types.has('OrderCancelled')?[{name:'Cancelado',state:'error'},...(types.has('PaymentRefunded')?[{name:'Reembolsado',state:'done'}]:[])]:[];
 return [...[
 ['Pedido','OrderPlaced',[]],['Comercial','OrderValidated',[]],
 ['Inventario','InventoryReserved',['InventoryUnavailable']],
 ['Pago','PaymentReceived',['PaymentRejected']],['Factura','InvoiceCreated',[]],
 ['Envío creado','ShipmentCreated',[]]
 ].map(([name,type,failures])=>({name,state:types.has(type)?'done':failures.some(t=>types.has(t))?'error':'idle'})),...closing];
}
