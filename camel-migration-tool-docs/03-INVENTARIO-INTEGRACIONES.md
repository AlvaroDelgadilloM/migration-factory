# Inventario de Integraciones

## Objetivo
Identificar qué sistemas externos dependen de la aplicación para evitar romper contratos durante la migración.

## REST
Registrar:

- URL
- método HTTP si puede inferirse
- headers
- autenticación
- timeout
- retry
- clase/ruta origen

## SOAP
Registrar:

- WSDL
- endpoint
- operación
- CXF bean
- namespaces
- autenticación

## JMS
Registrar:

- broker/provider
- JNDI
- queue/topic
- producer/consumer
- selector
- transaccionalidad
- DLQ

## Base de datos
Registrar:

- DataSource
- JNDI
- driver
- queries
- procedimientos almacenados
- transacciones

## Archivos
Registrar:

- file/ftp/sftp
- host/path
- polling
- filtros
- move/error folder

## Scheduling
Registrar Quartz/Timer y cron expressions.

## Salida de ejemplo

```text
Integration: Payment API
Type: REST
Source: PaymentRoute.java:58
Endpoint: http4://payments.internal/api/pay
Migration: camel-http
Risk: MEDIUM
```
