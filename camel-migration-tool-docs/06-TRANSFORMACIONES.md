# Transformaciones de Código

## Estrategia
Preferir transformaciones AST sobre búsqueda/reemplazo global.

## Dependencias
Transformar componentes Camel 2 a starters Camel 4 compatibles con Spring Boot.

## Dependency Injection
Legacy CDI:

```java
@Inject
private PaymentService paymentService;
```

Destino preferido:

```java
private final PaymentService paymentService;

public OrderRoute(PaymentService paymentService) {
    this.paymentService = paymentService;
}
```

## HTTP

Legacy:

```java
.to("http4://host/api")
```

Destino:

```java
.to("{{services.api.url}}")
```

## JNDI
Legacy:

```java
new InitialContext().lookup("java:/jdbc/LegacyDS")
```

Destino:

- Spring DataSource configuration
- application.yml
- variables de entorno

Si no existe suficiente información, generar TODO y marcar MANUAL.

## SQL
Externalizar cuando sea razonable y evitar concatenación dinámica insegura.

## Logging
No migrar logging de payloads sensibles sin revisión.

## Secrets
Detectar:

- password=
- token=
- apiKey=
- Authorization

Sustituir por variables de entorno y documentar el cambio.
