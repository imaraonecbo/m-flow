import test from 'node:test';
import assert from 'node:assert/strict';
import { AcquisitionEngine } from '../src/acquisition/engine.js';

function fakeLedger(){ return { append(){}, }; }
function fakeProviderStore(){ return { all(){ return [{id:'p1',name:'Authorized Provider',rail:'paypal',currencies:['KES'],regions:['KEN'],minCapital:'1000.00',maxCapital:'1000000.00',verified:true,active:true}]; } }; }

test('acquisition status is secret-safe', () => {
  const e = new AcquisitionEngine({ ledger: fakeLedger(), providerStore: fakeProviderStore() });
  const status = e.sourceStatus();
  const rendered = JSON.stringify(status);
  assert.equal(rendered.includes('SUPER-SECRET-MUST-NOT-LEAK'), false);
  assert.ok(status.ted); assert.ok(status.sam); assert.ok(status.opencorporates);
});

test('capital matcher returns only active verified compatible providers', () => {
  const e = new AcquisitionEngine({ ledger: fakeLedger(), providerStore: fakeProviderStore() });
  const matches = e.capitalMatches({ amount:'50000.00', currency:'KES', region:'KEN' });
  assert.equal(matches.length, 1);
  assert.equal(matches[0].id, 'p1');
});

test('normalized opportunity identifiers are deterministic', () => {
  const e = new AcquisitionEngine({ ledger: fakeLedger(), providerStore: fakeProviderStore() });
  const a = e.normalize({source:'ted',sourceId:'123',title:'IT services',buyerName:'Buyer',buyerCountry:'KEN',sector:['72000000'],estimatedValue:'1000.00',currency:'EUR',raw:{x:1}});
  const b = e.normalize({source:'ted',sourceId:'123',title:'IT services',buyerName:'Buyer',buyerCountry:'KEN',sector:['72000000'],estimatedValue:'1000.00',currency:'EUR',raw:{x:1}});
  assert.equal(a.id,b.id);
});
