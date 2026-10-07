# Seguridad

## Regla principal
La herramienta puede procesar repositorios corporativos sensibles. No debe enviar código fuente a servicios externos por defecto.

## Secret Detection
Detectar posibles:

- passwords
- tokens
- API keys
- certificates
- private keys
- connection strings

## Comportamiento

1. No copiar secretos al proyecto migrado.
2. Reemplazar por placeholders/variables de entorno.
3. Nunca imprimir el valor completo en logs.
4. Marcar hallazgo CRITICAL cuando aplique.

## Archivos sensibles

- .env
- *.jks
- *.p12
- *.pem
- id_rsa

No incluir en outputs salvo política explícita.

## SQL
Detectar concatenaciones de parámetros potencialmente inseguras.

## Logging
Detectar payload completo, Authorization headers y PII probable.
