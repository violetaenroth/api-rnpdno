from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
import hashlib
import base64
import json
from datetime import datetime
from enum import Enum
from loguru import logger

# clave fija usada para generar el token inicial
_CLAVE_FIJA_TOKEN = "z427FcQwMSPZuFbIjNWGDqUpw1MEo1DG7cIOBSuI3ps"


class AccionEndpoint(str, Enum):
    """Acciones válidas para la API."""
    GET_INFO_MATRIZ = "get_info_matriz"
    GET_INFO_LISTA = "get_info_lista"
    GET_PAGINADOR = "get_paginador"
    MUNICIPIOS = "municipios"
    TOKEN = "token"


def _evp_bytes_to_key(password: bytes, salt: bytes, key_len=32, iv_len=16) -> bytes:
    """Replica EVP_BytesToKey de OpenSSL (usado internamente por CryptoJS)."""
    m = []
    i = 0
    while len(b''.join(m)) < (key_len + iv_len):
        md = hashlib.md5()
        data = (m[i - 1] if i > 0 else b'') + password + salt
        md.update(data)
        m.append(md.digest())
        i += 1
    return b''.join(m)[:key_len + iv_len]


def generar_url_cryptojs(accion, data=None, token=None) -> str:
    """
    Genera URL cifrada replicando CryptoJS.AES.encrypt + btoa del JS.
    Args:
        accion: AccionEndpoint o string con la acción.
        data: Dict/list con los datos del payload.
        token: Clave de cifrado (JWT o clave fija).
    Returns:
        String base64 doble (listo para usar en la URL).
    """
    if token is None:
        raise ValueError("El parámetro 'token' es obligatorio")

    accion_str = accion.value if isinstance(accion, AccionEndpoint) else accion
    acciones_validas = {e.value for e in AccionEndpoint}
    if accion_str not in acciones_validas:
        raise ValueError(f"Acción '{accion_str}' no válida. Permitidas: {acciones_validas}")

    # getDay() (0-6, dom=0) y getMonth() (0-11)
    now = datetime.now()
    dia_semana = (now.weekday() + 1) % 7
    mes = now.month - 1
    fecha = f"{dia_semana}-{mes}-{now.year}"

    payload = {"fecha": fecha, "accion": accion_str, "data": data}
    json_str = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)

    # Cifrado AES-256-CBC con formato OpenSSL
    salt = get_random_bytes(8)
    password = token.encode('utf-8')
    key_iv = _evp_bytes_to_key(password, salt)
    key, iv = key_iv[:32], key_iv[32:]

    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = json_str.encode('utf-8')
    pad_len = 16 - (len(plaintext) % 16)
    plaintext += bytes([pad_len]) * pad_len
    ciphertext = cipher.encrypt(plaintext)

    openssl_format = b'Salted__' + salt + ciphertext
    encrypted_b64 = base64.b64encode(openssl_format).decode('utf-8')
    return base64.b64encode(encrypted_b64.encode('utf-8')).decode('utf-8')


def generar_token_endpoint() -> str:
    """Genera el endpoint cifrado para obtener un nuevo JWT."""
    return generar_url_cryptojs(AccionEndpoint.TOKEN, None, _CLAVE_FIJA_TOKEN)


def verificar_token(token: str) -> dict | None:
    """Decodifica un JWT y retorna su payload (sin verificar firma)."""
    try:
        payload_b64 = token.split('.')[1]
        padding = len(payload_b64) % 4
        if padding:
            payload_b64 += '=' * (4 - padding)
        return json.loads(base64.b64decode(payload_b64))
    except Exception:
        return None


def verificar_duracion_token(token):
    """Decodifica el JWT y muestra su info"""
    try:
        # JWT tiene 3 partes separadas por '.'
        # la segunda parte es el payload
        partes = token.split('.')
        payload = partes[1]
        
        # añadir padding si es necesario
        padding = len(payload) % 4
        if padding:
            payload += '=' * (4 - padding)
        
        # decodificar
        decoded = base64.b64decode(payload)
        data = json.loads(decoded)
        
        logger.info("Información del token:")
        logger.info(f"  Usuario: {data.get('username')}")
        logger.info(f"  Emitido (iat): {data.get('iat')}")
        logger.info(f"  Expira (exp): {data.get('exp')}")
        
        if 'iat' in data and 'exp' in data:
            duracion_segundos = data['exp'] - data['iat']
            duracion_minutos = duracion_segundos / 60
            logger.info(f"  ⏱️ Duración: {duracion_minutos:.0f} minutos ({duracion_segundos} segundos)")
        
        return data
        
    except Exception as e:
        logger.error(f"❌ Error decodificando: {e}")
        return None
