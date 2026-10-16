import { describe, expect, it } from 'vitest';
import { parseCases } from './automaticas';
import { changed, pretty } from './compare';

describe('Pruebas: casos', () => {
  it('accepts the CLI file and a plain list', () => {
    expect(parseCases('{"cases": [{"name": "a", "path": "/x"}]}')).toEqual({ cases: [{ name: 'a', path: '/x' }], error: null });
    expect(parseCases('[{"name": "a", "path": "/x", "method": "POST", "body": "<a/>"}]').cases.length).toBe(1);
    expect(parseCases('')).toEqual({ cases: [], error: null });
  });

  it('says what is wrong', () => {
    expect(parseCases('{"cases": [{"name": "a", "path": "x"}]}').error).toContain('caso 1');
    expect(parseCases('{"items": []}').error).toContain('"cases"');
    expect(parseCases('{nope').error).toContain('JSON inválido');
  });
});

describe('Pruebas: lado a lado', () => {
  it('lines up JSON and XML before comparing', () => {
    expect(pretty('{"a":1,"b":[2]}', 'application/json')).toEqual(['{', '  "a": 1,', '  "b": [', '    2', '  ]', '}']);
    expect(pretty('<r><a>1</a><b/></r>', 'text/xml')).toEqual(['<r>', '  <a>1</a>', '  <b/>', '</r>']);
    expect(pretty('', 'text/plain')).toEqual(['(sin cuerpo)']);
  });

  it('marks only the lines that changed on each side', () => {
    const [a, b] = changed(['{', '"points": 801', '"tier": "GOLD"', '}'], ['{', '"points": 801', '"tier": "PLATA"', '"extra": 1', '}']);
    expect([...a]).toEqual([2]);
    expect([...b]).toEqual([2, 3]);
  });
});
