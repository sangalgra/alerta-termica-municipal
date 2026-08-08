# Errores reales encontrados durante el desarrollo, y cómo resolverlos

Todos estos errores pasaron de verdad construyendo este proyecto. Se
documentan tal cual, con la solución que funcionó, para que si te pasa
lo mismo no tengas que investigarlo desde cero.

## 1. "No se puede cargar porque la ejecución de scripts está deshabilitada"
Al activar el entorno virtual en PowerShell (`.venv\Scripts\Activate.ps1`).

**Causa:** política de seguridad de Windows que bloquea la ejecución
de scripts `.ps1` por defecto.

**Solución** (una sola vez):
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## 2. "El término '.venv\Scripts\Activate.ps1' no se reconoce..."
**Causa:** en PowerShell hace falta anteponer `.\` para ejecutar un
script desde la carpeta actual — si no, PowerShell lo busca como si
fuera un comando del sistema, no un fichero local.

**Solución:**
```powershell
.\.venv\Scripts\Activate.ps1
```

## 3. El prompt muestra `(base)` y `(.venv)` a la vez, o `pip show` apunta a `anaconda3`
**Causa:** tener Anaconda activo (`base`) a la vez que el entorno
virtual del proyecto — Windows puede seguir usando el `pip`/`python`
de Anaconda aunque el venv esté "activado" visualmente.

**Solución:**
```powershell
conda deactivate
```
Ejecútalo **antes** de activar el `.venv`, no después. Si sigue
apareciendo `(base)`, repite el comando — a veces hace falta dos veces
si tienes Anaconda configurado para activarse automáticamente.

## 4. `OSError: No such file or directory` al instalar con pip, mencionando "Long Path"
Suele pasar instalando paquetes con muchos ficheros internos (como
Streamlit) en una ruta de proyecto ya larga.

**Causa:** límite clásico de Windows de 260 caracteres por ruta de
fichero, sin el soporte de rutas largas activado.

**Solución** (PowerShell como Administrador, una sola vez):
```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```
Reinicia VS Code después (no hace falta reiniciar el ordenador).

## 5. `AemetClientError: No se ha encontrado AEMET_API_KEY`
**Causa:** falta el fichero `.env`, o existe pero está vacío/mal
rellenado.

**Solución:**
```powershell
copy .env.example .env
notepad .env
```
Rellena `AEMET_API_KEY=tu_token_real` (sin comillas, sin espacios,
token gratuito en https://opendata.aemet.es/centrodedescargas/altaUsuario).

## 6. `429 Too Many Requests` o errores SSL puntuales llamando a AEMET
**Causa:** límite de peticiones de la API pública de AEMET, o caídas
de red puntuales del servidor — no es un fallo del código.

**Comportamiento esperado:** el cliente reintenta automáticamente (3
intentos con espera creciente). Si aun así falla, ese municipio
concreto se registra como error en el log y el resto de municipios
sigue procesándose con normalidad — no bloquea el pipeline. El control
de calidad (`src/quality/checks.py`) lo señala como aviso.

## 7. "No hay ficheros Raw ... pendientes" al ejecutar el pipeline dos veces seguidas
**Causa (bug real, ya corregido):** si AEMET o el INE devuelven "sin
cambios" respecto a la última extracción, no se crea ningún fichero
nuevo — y si ya no quedaba ningún fichero pendiente de antes (porque
se movió a `bkp/` en la ejecución anterior), Staging lo trataba como
un error fatal cuando en realidad es una situación normal.

**Solución:** ya está corregido en el código — si no hay ficheros
pendientes pero ya existe histórico válido en Staging, el pipeline
sigue adelante sin error. Si ves este error de verdad, significa que
no hay NINGÚN dato (ni nuevo ni histórico) — revisa que la ingesta
haya funcionado alguna vez.

## 8. La tabla del INE no tiene los municipios que esperabas
**Causa:** el INE publica esta estadística como **una tabla por
provincia**, no una tabla única de España. Si usas el id de tabla
equivocado, verás datos reales pero de otra provincia (nos pasó con
Murcia en vez de Alicante).

**Solución:** confirma el id de tabla correcto para tu provincia en
https://ine.es/jaxiT3/Datos.htm buscando "Población por sexo,
municipios y edad (año a año)" + el nombre de tu provincia, y
actualiza `config/settings.yaml` (`ine.tabla_id`).

## 9. `ModuleNotFoundError` al ejecutar pytest o los módulos de `src/`
**Causa:** no tienes el entorno virtual activado, o `requirements.txt`
cambió (se añadió una dependencia nueva) y no se ha reinstalado.

**Solución:**
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
