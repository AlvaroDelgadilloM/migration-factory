import { profileFor, runtimeEntry } from './version-picker';

const entry = (label: string, java25: boolean, key: string) => ({
  target: 'profile', profileId: key, runtime: 'spring', label,
  java: [{ version: '21', validated: true, reason: null }, { version: '25', validated: java25, reason: java25 ? null : 'Camel 4.14 sin Java 25' }],
  spring: [{ key, label, java: java25 ? ['21', '25'] : ['21'] }],
}) as any;

describe('Destino Spring Boot con varios perfiles', () => {
  const sb3 = entry('Spring Boot 3.5.16 + Camel 4.14.9', false, 'spring-boot-3.5-camel-4.14@3');
  const sb4 = entry('Spring Boot 4.1.1 + Camel 4.22.1', true, 'spring-boot-4.1-camel-4.22@1');
  const war = entry('Tomcat 10.1 WAR · Spring Boot 3.5.16', false, 'tomcat-10.1-war@1');

  it('ofrece Java 25 si algún perfil del runtime lo validó, con su línea de Spring', () => {
    const e = runtimeEntry([sb3, war, sb4], 'spring')!;
    expect(e.java.find((j: any) => j.version === '25')?.validated).toBe(true);
    expect(e.spring.filter((s: any) => s.java.includes('25')).map((s: any) => s.key)).toEqual(['spring-boot-4.1-camel-4.22@1']);
    expect(e.spring.map((s: any) => s.key)).not.toContain('tomcat-10.1-war@1');
  });

  it('elige el perfil por la línea de Spring o por el Java pedido', () => {
    const ps = [
      { id: 'a', key: 'spring-boot-3.5-camel-4.14', version: 3, runtime: 'spring', dependencyMapping: {} },
      { id: 'w', key: 'tomcat-10.1-war', version: 1, runtime: 'spring', dependencyMapping: { deployment: 'war' } },
      { id: 'b', key: 'spring-boot-4.1-camel-4.22', version: 1, runtime: 'spring', dependencyMapping: { javaVersions: ['21', '25'] } },
    ];
    expect(profileFor(ps, { runtime: 'spring', java: '25', spring: null })).toBe('b');
    expect(profileFor(ps, { runtime: 'spring', java: '21', spring: 'spring-boot-4.1-camel-4.22@1' })).toBe('b');
    expect(profileFor(ps, { runtime: 'spring', java: '21', spring: null })).toBe('a');
  });
});
