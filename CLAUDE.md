# CLAUDE.md — Monitor de Datacenter (SIY6122)

> Este archivo es el contexto permanente del proyecto. Léelo completo antes de generar o modificar código. Todas las decisiones aquí están **cerradas**: respétalas y no las cambies sin que te lo pidan explícitamente.

## 1. Qué es el proyecto

Sistema de monitoreo para una sala de networking / datacenter de una empresa pequeña. Vigila las condiciones críticas de la sala en tiempo real, y cuando algo se sale de rango lo muestra en un dashboard y avisa al encargado por correo, sin que nadie esté mirando la pantalla. Es un sistema tipo DCIM (Data Center Infrastructure Monitoring), no una maqueta de "salita con aire acondicionado".

Es un proyecto académico (Duoc UC, ramo Problemáticas Globales y Prototipado) que se evalúa en 3 parciales, pero es **un solo sistema que crece por capas — nada se rehace**.

## 2. Stack (cerrado)

- **Lenguaje:** Python.
- **Backend:** FastAPI (API REST + WebSocket).
- **Base de datos:** PostgreSQL (con SQLAlchemy + Pydantic).
- **Tiempo real:** WebSocket nativo de FastAPI (push al dashboard).
- **Autenticación:** login obligatorio, contraseñas hasheadas (passlib/bcrypt), tokens JWT (python-jose).
- **Correo:** envío de alertas por SMTP (config en .env).
- **Empaquetado:** Docker + docker-compose (app + Postgres levantan con un comando).
- **Frontend:** dos páginas HTML/JS servidas por FastAPI: `dashboard.html` (vista del encargado) y `panel.html` (simulador de sensores).

No usar: Node para el backend, Tinkercad, ni servicios cloud todavía (eso es P3).

## 3. Arquitectura y flujo (es un ETL)

panel.html (simulador) -> API (POST /lecturas) -> reglas.py (clasifica + prioriza)
|
v
PostgreSQL (lecturas + alertas) -> correo si crítico
|
v
dashboard.html <- WebSocket (push en tiempo real)



- **Extract:** el panel/simulador envía lecturas de las 5 variables.
- **Transform:** el backend clasifica cada variable y resuelve el estado general.
- **Load:** guarda lecturas y alertas en PostgreSQL (histórico).
- **Visualización:** dashboard en vivo por WebSocket + correo al encargado.

## 4. Las 5 variables y sus umbrales (cerrado)

Tres continuas (van con gauge) y dos de evento (van con estado).

| # | Variable | Tipo | NORMAL | ADVERTENCIA | CRÍTICO |
|---|---|---|---|---|---|
| 1 | Temperatura del aire (°C) | continua | 18–27 | 27–32 ó 15–18 | > 32 ó < 15 |
| 2 | Humedad relativa (%) | continua | 40–60 | 20–40 ó 60–70 | < 20 ó > 70 |
| 3 | Carga eléctrica UPS/PDU (%) | continua | < 70 | 70–90 | > 90 |
| 4 | Humo / incendio | evento | sin humo | — | humo detectado |
| 5 | Acceso físico (puerta + mantención) | evento | puerta cerrada | — | abierta sin mantención |

Notas de la variable 5: puerta abierta **con** modo mantención activo = acceso **autorizado** (estado normal, se registra como evento informativo); puerta abierta **sin** mantención = **intrusión** (crítico).

## 5. Regla de decisión (cerrado)

- **Humo detectado** o **acceso no autorizado** ⇒ estado general **CRÍTICO** siempre (no se promedian ni se esperan).
- En el resto: **gana el peor estado** — el estado general es el más severo de las 5 variables.
- Orden de severidad: NORMAL < ADVERTENCIA < CRÍTICO.
- Cuando el estado sube a crítico, se crea una alerta (se guarda) y se dispara el correo. Contar cada incidente crítico (flanco de subida, no en cada lectura).

## 6. Modelo de datos

- **usuarios**: id, username, password_hash, rol, creado_en.
- **lecturas**: id, timestamp, temperatura, humedad, carga_ups, humo (bool), puerta_abierta (bool), mantencion (bool), estado_general.
- **alertas**: id, timestamp, variable, severidad, mensaje, reconocida (bool).

`lecturas` es el histórico (se guarda cada lectura). `alertas` es el registro de eventos, que además sirve como log de auditoría.

## 7. Endpoints

- `POST /auth/login` → devuelve token JWT.
- `POST /lecturas` → recibe una lectura (protegido con token del dispositivo/simulador), corre reglas, guarda, evalúa alertas, dispara correo si crítico, hace broadcast por WebSocket. **No debe existir sin autenticación.**
- `GET /lecturas/ultima`, `GET /lecturas/historial`, `GET /alertas` → protegidos (dashboard autenticado).
- `POST /alertas/{id}/reconocer` → marca una alerta como reconocida.
- `WS /ws` → canal de tiempo real; empuja el estado a los dashboards conectados.
- Servir `dashboard.html` y `panel.html` como estáticos.

## 8. Seguridad por diseño (requisito duro — R9)

Nada queda abierto. Esto se construye desde el inicio, no se parcha al final.

- Login obligatorio para el dashboard; contraseñas **hasheadas** (bcrypt), nunca en texto plano.
- El endpoint de ingreso de lecturas exige token/API key del dispositivo. **No hay endpoints abiertos.**
- Secretos (clave JWT, credenciales DB, credenciales SMTP) **solo en `.env`**, nunca hardcodeados ni subidos al repo. Incluir `.env.example` sin valores reales.
- CORS restringido a los orígenes propios.
- El registro de alertas hace de log de auditoría (quién/qué/cuándo).
- HTTPS en producción (se agrega en P3, no en P1).
- Marco legal que justifica estas decisiones: Ley 21.663 (Marco de Ciberseguridad), Ley 21.719 (datos personales, privacidad por diseño), Ley 21.459 (delitos informáticos).

## 9. Frontend

- **dashboard.html**: relojes (temperatura, humedad, carga UPS), semáforo de estado general, tiles para humo/puerta/acceso/mantención, panel de alertas activas, contador de incidentes, registro de eventos. Se actualiza en vivo por WebSocket. (Ya existe una versión base con esta estética — reutilizarla y conectarla al backend, no rehacerla de cero.)
- **panel.html** (simulador de sensores): sliders para temperatura/humedad/carga UPS, toggles para humo/puerta/mantención, y **botones de escenario** para la demo: "ola de calor", "UPS sobrecargada", "humo detectado", "intrusión", "todo normal". Cada cambio hace `POST /lecturas`.

## 10. Estructura del proyecto

datacenter-monitor/
backend/
main.py # FastAPI: rutas, WebSocket, sirve estáticos
reglas.py # clasificación de las 5 variables + regla "gana el peor"
db.py # conexión, modelos SQLAlchemy, sesión
schemas.py # modelos Pydantic (entrada/salida)
auth.py # login, hashing, tokens JWT, dependencia de auth
correo.py # envío de alertas por SMTP
frontend/
dashboard.html
panel.html
requirements.txt
Dockerfile
docker-compose.yml # app + postgres
.env.example
README.md


## 11. Alcance por parcial (para no sobre-construir)

- **Parcial 1 (ahora):** todo corriendo **local** con `docker compose up`. Simulador + API + reglas + Postgres + dashboard en vivo + correo + login. La evidencia es el sistema andando y la demo con el panel. **No desplegar en AWS todavía.**
- **Parcial 2:** conexión "inalámbrica" entre componentes (el simulador manda por red) + planificación PMI (diagrama de componentes + carta Gantt).
- **Parcial 3:** despliegue en AWS + flujo ETL formal con diagramas + Design Thinking.

Construir siempre pensando en que P1 crece hacia P2 y P3 sin rehacer nada.

## 12. Convenciones

- Código y comentarios en español; type hints en Python.
- Dependencias sugeridas: `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `psycopg2-binary`, `pydantic`, `passlib[bcrypt]`, `python-jose[cryptography]`, `python-dotenv`, `aiosmtplib` (o `smtplib`).
- Priorizar que el sistema **corra y se pueda demostrar**, antes que features extra.
- Empezar por `reglas.py` con sus pruebas (los escenarios de la demo son casos de prueba), y recién después el resto.
