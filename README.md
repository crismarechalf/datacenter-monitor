# Monitor de Datacenter — SIY6122

Sistema de monitoreo (tipo **DCIM**) para la sala de networking de una empresa pequeña.
Vigila 5 variables críticas en tiempo real, las clasifica, guarda el histórico, muestra el
estado en un dashboard en vivo y **avisa al encargado por correo cuando algo se vuelve
crítico**, sin que nadie esté mirando la pantalla.

Proyecto académico de Duoc UC (Problemáticas Globales y Prototipado). Este repositorio
corresponde al **Parcial 1**: todo el sistema corriendo local con un solo comando.

---

## 1. Arquitectura (es un ETL)

```
panel.html (simulador)
      |  POST /lecturas          <- Extract
      v
   FastAPI  ->  reglas.py        <- Transform (clasifica y prioriza)
      |
      v
  PostgreSQL (lecturas + alertas) -> correo SMTP si es crítico   <- Load
      |
      v
dashboard.html  <-  WS /ws       <- Visualización en tiempo real
```

| Capa | Componente |
|---|---|
| Extract | `frontend/panel.html` — simulador de sensores |
| Transform | `backend/reglas.py` — clasificación + regla "gana el peor" |
| Load | `backend/db.py` — PostgreSQL (`lecturas`, `alertas`, `usuarios`) |
| Visualización | `frontend/dashboard.html` + WebSocket |
| Aviso | `backend/correo.py` — SMTP al encargado |

---

## 2. Las 5 variables y sus umbrales

| # | Variable | Tipo | NORMAL | ADVERTENCIA | CRÍTICO |
|---|---|---|---|---|---|
| 1 | Temperatura del aire (°C) | continua | 18–27 | 27–32 ó 15–18 | > 32 ó < 15 |
| 2 | Humedad relativa (%) | continua | 40–60 | 20–40 ó 60–70 | < 20 ó > 70 |
| 3 | Carga eléctrica UPS/PDU (%) | continua | < 70 | 70–90 | > 90 |
| 4 | Humo / incendio | evento | sin humo | — | humo detectado |
| 5 | Acceso físico (puerta + mantención) | evento | puerta cerrada | — | abierta sin mantención |

**Regla de decisión:** humo detectado o acceso no autorizado ⇒ **CRÍTICO siempre**. En el
resto **gana el peor estado** de las 5 variables (NORMAL < ADVERTENCIA < CRÍTICO).
Puerta abierta **con** mantención = acceso **autorizado** (no es crítico).
Los incidentes críticos se cuentan **por flanco de subida**, no en cada lectura.

Los límites de NORMAL y ADVERTENCIA son inclusivos y CRÍTICO es estricto: 27 °C es normal,
32 °C es advertencia, 32.1 °C es crítico.

---

## 3. Levantar el sistema

Requisitos: Docker y Docker Compose.

```bash
# 1. Configuración
cp .env.example .env

# 2. Generar los secretos y pegarlos en el .env
python -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('DEVICE_API_KEY=' + secrets.token_urlsafe(32))"
#    Definir además POSTGRES_PASSWORD, ADMIN_USER y ADMIN_PASSWORD.

# 3. Levantar app + base de datos
docker compose up --build
```

| URL | Qué es |
|---|---|
| http://localhost:8000/ | Dashboard del encargado |
| http://localhost:8000/panel | Simulador de sensores |
| http://localhost:8000/docs | Documentación interactiva de la API |

Se entra con el `ADMIN_USER` / `ADMIN_PASSWORD` del `.env`: el usuario se crea solo al
arrancar. **Si no defines esas dos variables no se crea ningún usuario** — a propósito, para
no dejar una credencial por defecto conocida.

Para apagar: `docker compose down` (agrega `-v` para borrar también el histórico).

---

## 4. Guion de la demo

Con el dashboard abierto en una ventana y el simulador en otra:

1. **Todo normal** — semáforo verde, los tres relojes en rango.
2. **Ola de calor** — temperatura 35 °C: el reloj se pone rojo, salta la alerta y sube el contador de incidentes.
3. **UPS sobrecargada** — carga 95 %: mismo camino por otra variable.
4. **Humo detectado** — crítico inmediato aunque el resto esté perfecto.
5. **Intrusión** — puerta abierta sin mantención: crítico.
6. **Mantención + puerta abierta** — el mismo sensor, ahora en verde: acceso autorizado.
7. **Reconocer** una alerta desde el dashboard y verla salir del panel de activas.

Todo lo que hace el simulador se refleja en el dashboard **al instante, sin recargar**: el
backend empuja el estado por WebSocket. El botón *envío automático (5 s)* deja el sistema
latiendo solo mientras se explica.

Para mostrar el correo hay que dejar `CORREO_HABILITADO=true` y completar las variables SMTP
(en Gmail: **contraseña de aplicación**, no la del correo). Con el correo deshabilitado el
sistema funciona igual y las alertas quedan en el log.

---

## 5. Endpoints

| Método | Ruta | Protección | Qué hace |
|---|---|---|---|
| POST | `/auth/login` | pública | Devuelve el token JWT |
| GET | `/auth/yo` | JWT | Usuario autenticado |
| POST | `/lecturas` | API key **o** JWT | Ingesta: reglas → guarda → alerta → correo → WebSocket |
| GET | `/lecturas/ultima` | JWT | Estado actual completo |
| GET | `/lecturas/historial?limite=` | JWT | Histórico de lecturas |
| GET | `/alertas?solo_activas=&limite=` | JWT | Registro de eventos / auditoría |
| POST | `/alertas/{id}/reconocer` | JWT | Marca una alerta como reconocida |
| WS | `/ws?token=` | JWT | Canal de tiempo real |

**No hay endpoints abiertos** salvo el login. Ejemplo de ingesta desde un dispositivo:

```bash
curl -X POST http://localhost:8000/lecturas \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <DEVICE_API_KEY del .env>" \
  -d '{"temperatura":35,"humedad":25,"carga_ups":60,"humo":false,"puerta_abierta":false,"mantencion":false}'
```

---

## 6. Seguridad por diseño (R9)

- Login obligatorio; contraseñas hasheadas con **bcrypt**, nunca en texto plano.
- `POST /lecturas` exige credencial: API key del dispositivo (header `X-API-Key`, comparada
  en tiempo constante) o JWT de usuario. El WebSocket también valida el token antes de aceptar.
- Secretos solo en `.env`, que está en `.gitignore`. `.env.example` va sin valores reales.
- Sin `JWT_SECRET_KEY` la aplicación **no arranca**: mejor caer que firmar con una clave por defecto.
- CORS restringido a los orígenes propios, nunca `*`.
- La tabla `alertas` funciona como log de auditoría: guarda qué pasó, cuándo y **quién** envió
  la lectura que lo gatilló.
- El contenedor corre con un usuario sin privilegios, no como root.
- HTTPS queda para producción (Parcial 3).

Marco legal que respalda estas decisiones: **Ley 21.663** (Marco de Ciberseguridad),
**Ley 21.719** (protección de datos personales, privacidad por diseño) y **Ley 21.459**
(delitos informáticos).

---

## 7. Pruebas

El motor de reglas tiene 21 pruebas: los escenarios de la demo, los bordes de cada umbral,
las 4 combinaciones de puerta × mantención y el conteo por flanco.

```bash
# Local, sin instalar nada (usa unittest de la biblioteca estándar)
python3 -m unittest discover -s backend -t backend -v

# O dentro del contenedor
docker compose exec app python -m unittest discover -t . -v
```

---

## 8. Estructura

```
datacenter-monitor/
├── backend/
│   ├── main.py           # FastAPI: rutas, WebSocket, estáticos
│   ├── reglas.py         # clasificación de las 5 variables + "gana el peor"
│   ├── db.py             # conexión, modelos SQLAlchemy, sesión
│   ├── schemas.py        # modelos Pydantic (entrada/salida)
│   ├── auth.py           # login, hashing, JWT, dependencias de auth
│   ├── correo.py         # envío de alertas por SMTP
│   └── test_reglas.py    # pruebas del motor de reglas
├── frontend/
│   ├── dashboard.html    # vista del encargado (relojes, semáforo, alertas)
│   └── panel.html        # simulador de sensores (sliders + escenarios)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 9. Notas

- `bcrypt` está fijado en `4.0.1`: passlib 1.7.4 no es compatible con 4.1+ y la aplicación
  no arranca si se instala una versión más nueva.
- Los rangos de `LecturaEntrada` (temperatura −50 a 100, humedad y carga 0 a 100) son de
  plausibilidad del sensor, no umbrales de negocio. Quien clasifica es siempre `reglas.py`.
- El contador de incidentes se reconstruye desde el histórico al arrancar, así un reinicio del
  contenedor no lo deja en cero ni inventa un incidente falso en la primera lectura crítica.

## 10. Qué viene

- **Parcial 2:** envío por red desde el simulador (la `DEVICE_API_KEY` ya está lista para eso)
  + planificación PMI (diagrama de componentes y carta Gantt).
- **Parcial 3:** despliegue en AWS, HTTPS y flujo ETL formal con diagramas + Design Thinking.
