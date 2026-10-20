import test from 'node:test';
import assert from 'node:assert/strict';
import {agentStates,eventStory,milestones,activityLine} from '../static/model.mjs';
const blank=()=>({events:[],records:[],activity:{agents:[],timeline:[]}});
test('sin evidencia los siete agentes quedan sin actividad',()=>{
 assert.equal(agentStates(blank()).length,7);
 assert.ok(agentStates(blank()).every(a=>a.state==='Sin actividad'));
 assert.ok(milestones(blank()).every(m=>m.state==='idle'));
});
test('no confunde un turno terminado con un envío entregado',()=>{
 const data=blank();data.activity.agents=[{id:'fulfillment',involved:true,status:'idle'}];
 data.events=[{eventType:'ShipmentCreated',source:'fulfillment'}];
 assert.equal(milestones(data).find(m=>m.name==='Envío creado').state,'done');
 assert.ok(eventStory(data.events[0])[1].includes('todavía no confirma'));
 assert.equal(agentStates(data).find(a=>a.id==='fulfillment').state,'Participó');
});
test('un faltante y un pago rechazado quedan visibles en ramas paralelas',()=>{
 const data=blank();data.events=[{eventType:'OrderValidated'},{eventType:'InventoryUnavailable'},{eventType:'PaymentRejected'}];
 assert.equal(milestones(data).find(m=>m.name==='Inventario').state,'error');
 assert.equal(milestones(data).find(m=>m.name==='Pago').state,'error');
 assert.equal(milestones(data).find(m=>m.name==='Factura').state,'idle');
});
test('una decisión pendiente hace visible el trabajo del CEO',()=>{
 const data=blank();data.records=[{decisionId:'D1',status:'PENDING'}];
 assert.equal(agentStates(data)[0].state,'Trabajo pendiente');
});
test('una tarea fallida no se presenta como completada',()=>{
 const data=blank();data.records=[{assignedTo:'fulfillment',status:'FAILED'}];
 assert.equal(agentStates(data).find(a=>a.id==='fulfillment').tone,'error');
});

test('el estado en vivo muestra la herramienta que usa el agente en este pedido',()=>{
 const data=blank();data.activity.agents=[{id:'fulfillment',onThisOrder:true,status:'working',tool:'reserve_inventory',involved:true}];
 const f=agentStates(data).find(a=>a.id==='fulfillment');
 assert.equal(f.state,'Ahora: reserva inventario');assert.equal(f.tone,'working');
});
test('un gerente que escaló queda esperando al CEO',()=>{
 const data=blank();data.activity.agents=[{id:'commercial-manager',waitingFor:'ceo',status:'waiting',involved:true}];
 assert.equal(agentStates(data).find(a=>a.id==='commercial-manager').state,'Esperando al CEO');
});
test('un agente ocupado en otro pedido no aparece trabajando en este',()=>{
 const data=blank();data.activity.agents=[{id:'accountant',busyElsewhere:true,status:'working'}];
 const a=agentStates(data).find(a=>a.id==='accountant');
 assert.equal(a.state,'Ocupado con otro pedido');assert.equal(a.tone,'idle');
});
test('la línea de actividad narra delegaciones, escalamientos y errores',()=>{
 assert.match(activityLine({agentId:'operations-manager',kind:'message',messageKind:'delegate',to:'fulfillment',text:'FulfillOrder O-1'}).text,/^delega a Especialista de despachos/);
 assert.equal(activityLine({agentId:'commercial-manager',kind:'message',messageKind:'escalate',to:'ceo',text:'x'}).tone,'escalate');
 assert.equal(activityLine({agentId:'fulfillment',kind:'tool_finished',ok:true,tool:'reserve_inventory'}),null);
 assert.match(activityLine({agentId:'fulfillment',kind:'tool_finished',ok:false,tool:'reserve_inventory',error:'INVENTORY_UNAVAILABLE'}).text,/INVENTORY_UNAVAILABLE/);
});

test('una cancelación por falta de stock y su reembolso se cuentan completos',()=>{
 const data=blank();data.events=[{eventType:'OrderValidated'},{eventType:'InventoryUnavailable'},
  {eventType:'OrderCancelled',source:'operations-manager',payload:{reason:'sin stock',inventoryReleased:false,refundDue:'540.00'}},
  {eventType:'PaymentRefunded',source:'accountant',payload:{amount:'540.00'}}];
 const names=milestones(data).map(m=>m.name+':'+m.state);
 assert.ok(names.includes('Cancelado:error'));assert.ok(names.includes('Reembolsado:done'));
 assert.match(eventStory(data.events[2])[1],/Contabilidad debe reembolsar 540\.00/);
 assert.match(eventStory(data.events[3])[1],/reembolsó 540\.00/);
});
