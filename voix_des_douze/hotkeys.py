"""Raccourcis Carbon enregistrés, sans surveillance des frappes ni permission Accessibilité."""
import ctypes as C


class EventType(C.Structure):
    _fields_ = [("eventClass", C.c_uint32), ("eventKind", C.c_uint32)]


class HotKeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


# Virtual key codes for function keys are independent of keyboard layout.
KEYS = {"F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
        "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111}


class GlobalHotkeys:
    def __init__(self, callback):
        self.callback = callback
        self.refs = []
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
        if result == 0 and identifier.id < len(self.actions):
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

    def close(self):
        for ref in self.refs:
            self.carbon.UnregisterEventHotKey(ref)
        self.refs.clear()
        if self.handler:
            self.carbon.RemoveEventHandler(self.handler)
            self.handler = None
