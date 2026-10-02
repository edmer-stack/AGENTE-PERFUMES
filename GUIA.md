# Guía de activación y uso · Agente de perfumes

## NOVEDAD · El agente ya busca solo (con navegador automático)
Ya **no tienes que fijar URLs**. El agente parte de la web principal de cada tienda (`web:` en `config.yaml`), abre un navegador Chromium sin ventana, escribe el perfume en el buscador, entra en el producto y lee el precio. Solo tienes que mantener la lista de perfumes.

**Instalación en tu PC (una vez):**
```
pip install -r requirements.txt
playwright install chromium
```
**Prueba:** `python agente.py --prueba` (tarda 10-20 min la primera vez). Con `--visible` ves el navegador trabajando.
Al terminar imprime una **COBERTURA POR TIENDA** (cuántos perfumes encontró en cada una y por qué falló donde falló), y la repite en el informe.
Ya no necesitas `diagnostico.py`.

**Horario:** el agente arranca hacia las 9:30 y el informe llega sobre las 10:00.
**GitHub Actions:** el workflow ya instala el navegador solo; no cambies nada más.


## Qué hace
Cada día a las 10:00 (Madrid) busca tus perfumes en las tiendas de `config.yaml`, calcula el **precio total real** (precio + envío + IVA/aranceles/gestión si es de fuera de la UE), guarda el histórico, lo analiza y te envía por **email** un informe en tabla con gráficas y por **WhatsApp** un resumen corto.

Coste: **0 €** (GitHub Actions + Gmail + CallMeBot).

---

## Activación (una sola vez, ~20 minutos)

### Paso 1 · Contraseña de aplicación de Gmail
1. Activa la verificación en dos pasos en tu cuenta Google (si no la tienes).
2. Entra en https://myaccount.google.com/apppasswords, crea una con el nombre "perfumes" y copia las 16 letras.

### Paso 2 · Activar el WhatsApp gratuito (CallMeBot)
1. Entra en https://www.callmebot.com/blog/free-api-whatsapp-messages/ y sigue las instrucciones: añades el contacto que indican y le envías un mensaje concreto por WhatsApp.
2. Te responderán con tu **apikey**. Guárdala.
   *(Es un servicio gratuito no oficial de WhatsApp: si algún día falla, el email seguirá funcionando.)*

### Paso 3 · Subir el proyecto a GitHub
1. Crea una cuenta en https://github.com (gratis) y un repositorio nuevo **privado** llamado `agente-perfumes`.
2. Descomprime el ZIP y sube TODO su contenido al repositorio (botón *Add file → Upload files*; arrastra también la carpeta `.github`).

### Paso 4 · Guardar tus datos privados (Secrets)
En el repositorio: *Settings → Secrets and variables → Actions → New repository secret*. Crea estos cinco:

| Nombre | Valor |
|---|---|
| `EMAIL_USER` | tu Gmail (el que envía) |
| `EMAIL_PASS` | las 16 letras del paso 1 |
| `EMAIL_TO` | el email donde quieres el informe |
| `WA_PHONE` | tu móvil con prefijo, p. ej. `+34XXXXXXXXX` |
| `WA_APIKEY` | la apikey del paso 2 |

### Paso 5 · Primera ejecución de prueba
*Actions → informe-diario → Run workflow.* En 2-3 minutos deberías recibir el email. Si no llega, abre la ejecución y lee el log.

**Es normal que en la primera prueba algunas tiendas no respondan o no encuentren el producto** (ver "Limitaciones"). Cuando pase:
1. Abre en tu navegador la página del perfume en cada tienda.
2. Copia la URL y pégala en `config.yaml` en `urls:` de ese perfume, por ejemplo:
   ```yaml
   urls: { notino: "https://www.notino.es/...", primor: "https://www.primor.eu/..." }
   ```
   Con la URL fijada el agente ya no depende de la búsqueda y es mucho más fiable.

A partir de ahí funciona solo cada día.

---

## Uso diario
- **Leer el informe:** tabla resumen arriba (mejor total, tienda, precio objetivo, veredicto) y debajo, por perfume, su gráfica y las 4 mejores ofertas.
- **Cambiar o añadir perfumes:** edita `config.yaml` (en GitHub: abre el archivo → icono del lápiz) y copia un bloque de `perfumes:`. Puedes tener los que quieras.
- **Añadir una tienda:** copia un bloque de `tiendas:` con su URL de búsqueda y costes de envío.
- **Segunda mano / decants:** añade la tienda con `tipo: segunda_mano` (o `decant`) y `garantia: true`, y fija la URL concreta en `urls:`. Sin `garantia: true` se ignora.
- **Probar en tu PC:** `pip install -r requirements.txt` y `python agente.py --prueba` (no envía nada; genera `data/ultimo_informe.html`).

## Cómo interpretar los veredictos
| Veredicto | Cuándo lo da |
|---|---|
| 🟢 **Compra inmediata** | El total está en el 15 % más barato de su histórico o a ≤3 % de su mínimo (no exige el mínimo absoluto). |
| 🟡 **Permanecer en espera** | Precio normal (entre medio y bajo) o con tendencia a la baja. |
| 🔴 **Desestimar compra** | Está en el 25 % más caro del histórico o >10 % por encima de su mediana. |

El **precio objetivo** lo calcula el agente (percentil 20 de su histórico; con poco histórico, 8 % por debajo de la mediana de tiendas). La **confianza** sube conforme se acumulan días de datos.

---

## Limitaciones (importante)
1. **Histórico de 24 meses:** no existe una fuente gratuita fiable que lo dé de perfumes. El agente construye **su propio histórico desde el primer día**, así que las gráficas empezarán cortas y se irán completando. Los primeros 14 días trabaja en "modo arranque" con la comparación entre tiendas.
2. **Bloqueos:** algunas tiendas (Sephora, Douglas, Marvimundo…) pueden bloquear también al navegador automático. No hay forma gratuita de garantizarlo; el informe muestra qué tiendas respondieron. Si una tienda bloquea siempre, desactívala con `activa: false`.
3. **Costes de envío, aranceles y aduana** son valores de `config.yaml`, no leídos de la web. Compruébalos una vez y ajústalos. Para tiendas de fuera de la UE, verifica el arancel vigente y la tasa de gestión de tu transportista.
4. **Autenticidad:** el agente no puede verificarla. Por eso solo usa las tiendas que tú listas en config; añade únicamente vendedores fiables.
5. **Hora de envío:** GitHub puede retrasar unos minutos las tareas programadas; llegará sobre las 10:00-10:30.
6. **Fichas y alternativas (clones):** se añadirán en `alternativas:` tras estudiar cada perfume; el agente las vigila y solo las muestra si su total es ≤ 70 €.
