# P1 — StubLeakGuard

## Objetivo

Evitar que dependencias ficticias creadas para OpenRewrite terminen influyendo en la validación o artifact final.

## Gate

Crear:

```text
StubLeakGuard
```

Ejecutar antes de:

```text
final dependency resolution
compile
test
package
ZIP generation
PR generation
```

## Validaciones

Comprobar que:

- candidate POM no contiene repositorios stub;
- candidate POM no contiene artifacts ficticios;
- dependencyManagement no contiene stubs;
- `.m2-degraded` no está dentro del candidate;
- classpath final no usa el overlay;
- Maven final se ejecuta sin `use_local_overlay`;
- ZIP final no contiene stubs.

## Resultado

```text
STUB_LEAK_GUARD=PASS
```

o:

```text
STUB_LEAK_DETECTED
```

con evidencia.

## Evidencia sugerida

```json
{
  "stubbedDependencies": [
    "com.oracle:ojdbc6:11.2.0.4"
  ],
  "overlayEnabled": false,
  "candidateContainsStubRepo": false,
  "finalClasspathContainsStub": false
}
```

## Criterios de aceptación

- El build final no puede pasar gracias a un stub.
- El ZIP nunca contiene `.m2-degraded`.
- El guard debe bloquear packaging si detecta fuga.
