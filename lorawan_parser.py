"""
LoRaWAN MAC layer parser — passive sniffing with optional decryption.

Parses: MHDR, Join Request, Join Accept (with AppKey), Data frames.
When app_key is provided:
  - JoinAccept is decrypted and parsed (AppNonce, NetID, DevAddr, session keys derived)
  - FRMPayload is decrypted using derived session keys (AppSKey / NwkSKey)

Session state (NwkSKey, AppSKey, DevAddr) is kept as module-level variables and
updated whenever a JoinRequest + JoinAccept pair is seen with a valid AppKey.
"""

import struct

try:
    from Crypto.Cipher import AES as _AES
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


MTYPE = {
    0b000: "JoinRequest",
    0b001: "JoinAccept",
    0b010: "UnconfDataUp",
    0b011: "UnconfDataDown",
    0b100: "ConfDataUp",
    0b101: "ConfDataDown",
    0b110: "RFU",
    0b111: "Proprietary",
}

# ── Session state ─────────────────────────────────────────────────────────────
# Updated when a JoinRequest is seen (stores DevNonce) and when a JoinAccept is
# successfully decrypted (stores derived session keys).
_session: dict = {
    "dev_nonce":  None,   # int: last seen DevNonce from JoinRequest
    "app_nonce":  None,   # bytes(3): AppNonce from last decrypted JoinAccept
    "net_id":     None,   # bytes(3): NetID from last decrypted JoinAccept
    "dev_addr":   None,   # int: DevAddr from last decrypted JoinAccept
    "nwk_skey":   None,   # bytes(16)
    "app_skey":   None,   # bytes(16)
}


# ── AES helpers ───────────────────────────────────────────────────────────────

def _aes128_encrypt(key: bytes, block: bytes) -> bytes:
    """AES-128 ECB encrypt a single 16-byte block."""
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("pycryptodome is not installed; cannot perform AES operations")
    cipher = _AES.new(key, _AES.MODE_ECB)
    return cipher.encrypt(block)


def _derive_session_keys(
    app_key: bytes,
    app_nonce: bytes,
    net_id: bytes,
    dev_nonce: int,
) -> tuple[bytes, bytes]:
    """
    Derive NwkSKey and AppSKey per LoRaWAN 1.0.x spec:
      NwkSKey = AES128_encrypt(AppKey, 0x01 | AppNonce | NetID | DevNonce | pad7)
      AppSKey = AES128_encrypt(AppKey, 0x02 | AppNonce | NetID | DevNonce | pad7)
    AppNonce: 3 bytes LE, NetID: 3 bytes LE, DevNonce: 2 bytes LE, pad: 7 zero bytes.
    """
    dev_nonce_b = struct.pack("<H", dev_nonce)
    base = app_nonce + net_id + dev_nonce_b + b"\x00" * 7  # 3+3+2+7 = 15 bytes
    nwk_skey = _aes128_encrypt(app_key, b"\x01" + base)
    app_skey = _aes128_encrypt(app_key, b"\x02" + base)
    return nwk_skey, app_skey


def _decrypt_frm_payload(
    key: bytes,
    dev_addr: int,
    fcnt: int,
    direction: int,   # 0 = uplink, 1 = downlink
    frm: bytes,
) -> bytes:
    """
    Decrypt (or encrypt — same operation) FRMPayload using LoRaWAN AES-128 CTR mode.
    For each block k (1-based):
      A_k = 0x01 | 0x00*4 | dir(1) | DevAddr(4 LE) | FCnt(4 LE) | 0x00 | k(1)
      S_k = AES128_encrypt(key, A_k)
    Plaintext = FRMPayload XOR S_1||S_2||...
    """
    result = bytearray()
    for k in range(1, (len(frm) + 15) // 16 + 1):
        a_k = (
            b"\x01"
            + b"\x00\x00\x00\x00"
            + struct.pack("<B", direction)
            + struct.pack("<I", dev_addr)
            + struct.pack("<I", fcnt)
            + b"\x00"
            + struct.pack("<B", k)
        )
        s_k = _aes128_encrypt(key, a_k)
        result.extend(s_k)
    return bytes(x ^ y for x, y in zip(frm, result))


# ── Public API ────────────────────────────────────────────────────────────────

def parse(payload: bytes, app_key: bytes | None = None) -> dict:
    """
    Parse a LoRaWAN MAC frame.

    Returns a dict of human-readable fields.  Always sets "MType" if the frame
    has at least 1 byte.  Sets "error" key for structural problems but still
    returns whatever fields could be parsed.

    app_key: optional 16-byte AppKey for decrypting JoinAccept and FRMPayload.
    """
    if not payload:
        return {"error": "empty payload"}

    mhdr  = payload[0]
    mtype = (mhdr >> 5) & 0x7
    major = mhdr & 0x3

    result: dict = {"MType": MTYPE.get(mtype, "Unknown"), "Major": major}

    # ── Join Request (unencrypted, only MIC-protected) ────────────────────────
    if mtype == 0b000:
        if len(payload) < 23:
            result["error"] = f"JoinRequest too short ({len(payload)}B, expected 23)"
            return result
        dev_nonce_val = struct.unpack_from("<H", payload, 17)[0]
        result.update({
            "AppEUI":   payload[1:9][::-1].hex(),    # little-endian on wire
            "DevEUI":   payload[9:17][::-1].hex(),
            "DevNonce": f"0x{dev_nonce_val:04x}",
            "MIC":      payload[19:23].hex(),
        })
        # Store DevNonce for session key derivation when JoinAccept arrives
        _session["dev_nonce"] = dev_nonce_val

    # ── Join Accept ───────────────────────────────────────────────────────────
    elif mtype == 0b001:
        # JoinAccept body is AES-encrypted with AppKey (using *encrypt* to decrypt)
        # Wire format: MHDR(1) + encrypted_payload(12 or 28) + MIC(4)
        # The encrypted blob covers AppNonce(3) + NetID(3) + DevAddr(4) + DLSettings(1)
        #   + RxDelay(1) [+ CFList(16)] + MIC(4)
        # Decryption: AES128_ECB_encrypt(AppKey, ciphertext)
        encrypted = payload[1:]  # everything after MHDR, including MIC
        if app_key is not None and _CRYPTO_AVAILABLE:
            if len(encrypted) not in (12, 28):
                result["error"] = (
                    f"JoinAccept encrypted body length {len(encrypted)}B "
                    f"(expected 12 or 28)"
                )
                result["raw_hex"] = encrypted.hex()
                return result
            # Decrypt: AES encrypt (not decrypt) per LoRaWAN spec
            # Process in 16-byte blocks; pad last block if needed (shouldn't happen for 12/28)
            decrypted = bytearray()
            for i in range(0, len(encrypted), 16):
                block = encrypted[i:i+16]
                if len(block) < 16:
                    block = block + b"\x00" * (16 - len(block))
                decrypted.extend(_aes128_encrypt(app_key, bytes(block)))
            decrypted = bytes(decrypted[:len(encrypted)])

            # Parse decrypted content (MIC is at the end of decrypted)
            # Layout: AppNonce(3) NetID(3) DevAddr(4) DLSettings(1) RxDelay(1) [CFList(16)] MIC(4)
            if len(decrypted) < 12:
                result["error"] = "JoinAccept decrypt yielded too few bytes"
                return result

            app_nonce_b = decrypted[0:3]
            net_id_b    = decrypted[3:6]
            dev_addr_val = struct.unpack_from("<I", decrypted, 6)[0]
            dl_settings  = decrypted[10]
            rx_delay     = decrypted[11]
            mic          = decrypted[-4:]

            result.update({
                "AppNonce":   app_nonce_b[::-1].hex(),  # display as big-endian
                "NetID":      net_id_b[::-1].hex(),
                "DevAddr":    f"{dev_addr_val:08x}",
                "DLSettings": f"0x{dl_settings:02x}",
                "RxDelay":    rx_delay,
                "MIC":        mic.hex(),
            })

            if len(decrypted) >= 28:
                result["CFList"] = decrypted[12:28].hex()

            # Derive session keys if we have a DevNonce from the last JoinRequest
            dev_nonce_val = _session.get("dev_nonce")
            if dev_nonce_val is not None:
                try:
                    nwk_skey, app_skey = _derive_session_keys(
                        app_key, app_nonce_b, net_id_b, dev_nonce_val
                    )
                    _session["app_nonce"] = app_nonce_b
                    _session["net_id"]    = net_id_b
                    _session["dev_addr"]  = dev_addr_val
                    _session["nwk_skey"]  = nwk_skey
                    _session["app_skey"]  = app_skey
                    result["NwkSKey"] = nwk_skey.hex()
                    result["AppSKey"] = app_skey.hex()
                    result["session_note"] = "Session keys derived and stored"
                except Exception as e:
                    result["session_error"] = str(e)
            else:
                result["session_note"] = (
                    "DevNonce unknown — no prior JoinRequest seen; session keys not derived"
                )
        else:
            result["note"] = "JoinAccept body is AES-encrypted (AppKey required for decryption)"

    # ── Data frames (uplink/downlink, confirmed/unconfirmed) ─────────────────
    elif mtype in (0b010, 0b011, 0b100, 0b101):
        if len(payload) < 8:
            result["error"] = f"data frame too short ({len(payload)}B, minimum 8)"
            return result

        dev_addr  = struct.unpack_from("<I", payload, 1)[0]
        fctrl     = payload[5]
        fcnt      = struct.unpack_from("<H", payload, 6)[0]
        fopts_len = fctrl & 0x0F
        direction = "uplink" if mtype in (0b010, 0b100) else "downlink"
        dir_int   = 0 if direction == "uplink" else 1

        result.update({
            "Direction": direction,
            "DevAddr":   f"{dev_addr:08x}",
            "FCnt":      fcnt,
            "FCtrl": {
                "ADR":       bool(fctrl & 0x80),
                "ADRACKReq": bool(fctrl & 0x40) if direction == "uplink" else False,
                "ACK":       bool(fctrl & 0x20),
                "FPending":  bool(fctrl & 0x10) if direction == "downlink" else False,
                "FOpts_len": fopts_len,
            },
        })

        if fopts_len:
            fopts_start = 8
            fopts_end   = fopts_start + fopts_len
            result["FOpts_hex"] = payload[fopts_start:fopts_end].hex()

        body_start = 8 + fopts_len
        # Subtract 4-byte MIC from end
        if len(payload) > body_start + 4:
            fport = payload[body_start]
            result["FPort"] = fport
            frm = payload[body_start + 1 : len(payload) - 4]
            if frm:
                result["FRMPayload_hex"] = frm.hex()

                # Attempt decryption if we have session keys and DevAddr matches
                decrypted_frm: bytes | None = None
                if (
                    app_key is not None
                    and _CRYPTO_AVAILABLE
                    and _session.get("nwk_skey") is not None
                    and _session.get("app_skey") is not None
                ):
                    # Use AppSKey for FPort > 0, NwkSKey for FPort == 0
                    session_dev_addr = _session.get("dev_addr")
                    if session_dev_addr is None or session_dev_addr == dev_addr:
                        key = (
                            _session["app_skey"] if fport > 0
                            else _session["nwk_skey"]
                        )
                        try:
                            decrypted_frm = _decrypt_frm_payload(
                                key, dev_addr, fcnt, dir_int, frm
                            )
                            result["FRMPayload_decrypted"] = decrypted_frm.hex()
                            result["FRMPayload_key"] = (
                                "AppSKey" if fport > 0 else "NwkSKey"
                            )
                        except Exception as e:
                            result["FRMPayload_decrypt_error"] = str(e)
                            result["FRMPayload_note"] = (
                                "encrypted — needs AppSKey (FPort>0) or NwkSKey (FPort=0)"
                            )
                    else:
                        result["FRMPayload_note"] = (
                            f"encrypted — DevAddr {dev_addr:08x} doesn't match "
                            f"session DevAddr {session_dev_addr:08x}"
                        )
                else:
                    result["FRMPayload_note"] = (
                        "encrypted — needs AppSKey (FPort>0) or NwkSKey (FPort=0)"
                    )

        if len(payload) >= 4:
            result["MIC"] = payload[-4:].hex()

    return result
