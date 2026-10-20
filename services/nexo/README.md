# Nexo: tienda de demostración

Con el stack local encendido (ver `local/README.md`), inicia la tienda en tu equipo:

```sh
cd local && ./nexo
# abre http://127.0.0.1:8090
```

La interfaz está en español y permite:

- Consultar los seis productos con precios y existencias reales del Company MCP.
- Preparar una compra normal, un descuento del 25%, un faltante de inventario o un pedido duplicado.
- Modificar cantidades, cliente y descuento antes de confirmar la compra.
- Ver los siete agentes organizados por departamento con su estado en vivo para ese pedido (por ejemplo
  «Ahora: reserva inventario», «Esperando al CEO», «Ocupado con otro pedido»), sus tareas y la línea
  de tiempo *En vivo* de herramientas, delegaciones, escalamientos y decisiones.
- Seguir los eventos de un pedido, inspeccionar decisiones y abrir las conversaciones de Hermes.
- Consultar pedidos generados por `./sim` introduciendo su identificador.

Cada compra llama a `place_order` con la identidad `order-simulator`. El servidor calcula el importe
con los precios del catálogo y aplica al pago simulado el descuento solicitado. Si el CEO rechaza
ese descuento, el gerente comercial cancela el pedido (el cliente pagó el precio con descuento).
No hay cobros reales. La opción de pedido duplicado hace dos envíos con el mismo identificador;
los reintentos de una compra sin confirmación conservan también ese identificador. La lista de compras
y la compra pendiente se guardan en el navegador; los registros de negocio permanecen en DynamoDB.

El seguimiento se actualiza cada dos segundos y muestra evidencia registrada, no animaciones
que simulen éxito. Operaciones y Contabilidad pueden avanzar en paralelo. Un envío creado no
implica que haya sido despachado o entregado. Los estados del equipo corresponden únicamente
al pedido seleccionado. El escenario de faltante solicita tres estaciones USB-C; comprueba las
existencias actuales antes de presentar, pues los pedidos anteriores pueden consumir inventario.

**Cargar conversaciones** exporta las sesiones de los siete contenedores usando la CLI de Hermes,
como el usuario `hermes`, a directorios temporales con redacción de secretos. Incluye eventos,
argumentos de herramientas, resultados y explicaciones registradas en su idioma original; no añade
almacenamiento duradero de conversaciones ni expone razonamiento privado del modelo. Las sesiones
pueden perderse al recrear contenedores, y los logs están sujetos a la retención de Docker.

La tienda requiere `uv`, Docker y el stack local; se enlaza exclusivamente a loopback. Para que
el pedido avance deben estar encendidos los agentes y vigentes sus credenciales de Bedrock.
La prueba de reinicio sigue disponible mediante `./sim run 05` y los comandos de la sección
«Failure drills» de `local/README.md`; su pedido también puede consultarse en la tienda.

Verificación:

```sh
cd services/nexo
uv run pytest
node --test tests/model.test.mjs
```

## Cómo funciona

- `server.py`: servidor solo en loopback (127.0.0.1:8090). Coloca pedidos con `place_order` en el
  Company MCP (identidad `order-simulator`), lee eventos (registro de auditoría de EventBridge en
  LocalStack), registros (DynamoDB) y la actividad en vivo del servicio `agent-activity`
  (`AGENT_ACTIVITY_URL`). Las conversaciones se exportan con `docker compose exec` en los contenedores
  de los agentes, por eso Nexo corre en el equipo y no dentro de compose.
- En AWS corre como servicio de ECS Express Mode con URL pública HTTPS (`infrastructure/modules/nexo`):
  escucha en `NEXO_HOST=0.0.0.0`, pide autenticación básica con `NEXO_PASSWORD` (parámetro de SSM,
  cualquier usuario), firma sus llamadas al MCP con el rol `order-simulator` (`SIMULATOR_AUTH=iam`) y
  lee la traza con `TABLE_NAME` y `BUSINESS_EVENTS_LOG_GROUP`. Las conversaciones vienen del archivo de
  sesiones en S3 (`TRANSCRIPTS=s3`): después de cada turno, cada agente exporta su sesión de Hermes a
  `sessions/archives/<agente>/<pedido>/`. `/health` responde sin contraseña para el balanceador. La imagen se
  construye con `Dockerfile` (ver `infrastructure/README.md`).
- `static/`: la interfaz (HTML, CSS y módulos JavaScript sin dependencias); `model.mjs` traduce eventos
  y actividad a textos en español.
- Los estilos de los agentes y los milestones reflejan evidencia registrada: un pedido cancelado
  muestra «Cancelado» y, si hubo reembolso, «Reembolsado».
