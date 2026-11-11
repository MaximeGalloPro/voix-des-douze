"""Raccourcis Carbon enregistrés, sans surveillance des frappes ni permission Accessibilité."""
import ctypes as C


class EventType(C.Structure):
    _fields_ = [("eventClass", C.c_uint32), ("eventKind", C.c_uint32)]


class HotKeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


# Virtual key codes for function keys are independent of keyboard layout.
KEYS = {"F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
        "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111}


def colon_binding(lib):
    """Resolve ':' in the current macOS layout, including Shift when required."""
    lib.TISCopyCurrentKeyboardLayoutInputSource.restype = C.c_void_p
    lib.TISGetInputSourceProperty.argtypes = [C.c_void_p, C.c_void_p]
    lib.TISGetInputSourceProperty.restype = C.c_void_p
    lib.CFDataGetBytePtr.argtypes = [C.c_void_p]
    lib.CFDataGetBytePtr.restype = C.c_void_p
    lib.CFRelease.argtypes = [C.c_void_p]
    lib.UCKeyTranslate.argtypes = [C.c_void_p, C.c_uint16, C.c_uint16, C.c_uint32,
        C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32), C.c_uint32,
        C.POINTER(C.c_uint32), C.POINTER(C.c_uint16)]
    source = lib.TISCopyCurrentKeyboardLayoutInputSource()
    try:
        prop = C.c_void_p.in_dll(lib, "kTISPropertyUnicodeKeyLayoutData")
        data = lib.TISGetInputSourceProperty(source, prop)
        if data:
            layout = lib.CFDataGetBytePtr(data)
            for modifiers in (0, 2):
                for key in range(128):
                    dead, length = C.c_uint32(), C.c_uint32()
                    chars = (C.c_uint16 * 8)()
                    result = lib.UCKeyTranslate(layout, key, 0, modifiers,
                        lib.LMGetKbdType(), 1, C.byref(dead), 8, C.byref(length), chars)
                    if result == 0 and length.value == 1 and chars[0] == ord(":"):
                        return key, modifiers << 8
        raise RuntimeError("Touche : introuvable dans la disposition clavier actuelle.")
    finally:
        if source:
            lib.CFRelease(source)


class GlobalHotkeys:
    def __init__(self, callback):
        self.callback = callback
        self.refs = []
        self.debug_ref = C.c_void_p()
        self.carbon = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        lib = self.carbon
        lib.GetApplicationEventTarget.restype = C.c_void_p
        self.callback_type = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
        lib.InstallEventHandler.argtypes = [C.c_void_p, self.callback_type, C.c_uint32,
                                           C.POINTER(EventType), C.c_void_p, C.POINTER(C.c_void_p)]
        lib.RegisterEventHotKey.argtypes = [C.c_uint32, C.c_uint32, HotKeyID, C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
        lib.UnregisterEventHotKey.argtypes = [C.c_void_p]
        lib.RemoveEventHandler.argtypes = [C.c_void_p]
        lib.GetEventParameter.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p, C.c_uint32, C.c_void_p, C.c_void_p]
        self.handler_fn = self.callback_type(self._event)
        self.handler = C.c_void_p()
        event = EventType(int.from_bytes(b"keyb", "big"), 6)
        result = lib.InstallEventHandler(lib.GetApplicationEventTarget(), self.handler_fn, 1, C.byref(event), None, C.byref(self.handler))
        if result:
            raise RuntimeError(f"Installation des raccourcis impossible ({result})")
        self.actions = []

    def _event(self, _, event, __):
        identifier = HotKeyID()
        result = self.carbon.GetEventParameter(event, int.from_bytes(b"----", "big"),
                    int.from_bytes(b"hkid", "big"), None, C.sizeof(identifier), None, C.byref(identifier))
        if result == 0:
            if identifier.id == 1000:
                self.callback("resource_debug")
            elif identifier.id < len(self.actions):
                self.callback(self.actions[identifier.id])
        return 0

    def configure(self, bindings):
        for ref in self.refs:
            self.carbon.UnregisterEventHotKey(ref)
        self.refs.clear()
        self.actions = list(bindings)
        errors = []
        for index, (action, key) in enumerate(bindings.items()):
            ref = C.c_void_p()
            if key not in KEYS:
                errors.append(f"Touche inconnue : {key}")
                continue
            # Control + Option, never Command: no Qt modifier translation.
            result = self.carbon.RegisterEventHotKey(KEYS[key], 4096 | 2048,
                    HotKeyID(int.from_bytes(b"VDDZ", "big"), index),
                    self.carbon.GetApplicationEventTarget(), 0, C.byref(ref))
            if result:
                errors.append(f"Contrôle+Option+{key} indisponible ({result})")
            else:
                self.refs.append(ref)
        return errors

    def set_resource_debug(self, enabled):
        if self.debug_ref:
            self.carbon.UnregisterEventHotKey(self.debug_ref)
            self.debug_ref = C.c_void_p()
        if enabled:
            key, modifiers = colon_binding(self.carbon)
            result = self.carbon.RegisterEventHotKey(key, modifiers,
                HotKeyID(int.from_bytes(b"VDDZ", "big"), 1000),
                self.carbon.GetApplicationEventTarget(), 0, C.byref(self.debug_ref))
            if result:
                raise RuntimeError(f"Raccourci : indisponible ({result}).")
            return key, modifiers

    def close(self):
        self.set_resource_debug(False)
        for ref in self.refs:
            self.carbon.UnregisterEventHotKey(ref)
        self.refs.clear()
        if self.handler:
            self.carbon.RemoveEventHandler(self.handler)
            self.handler = None
