"""Activité utilisateur macOS limitée à la durée des workers, sans empêcher la veille."""
import ctypes as C


class UserActivity:
    def __enter__(self):
        C.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
        self.objc = C.CDLL("/usr/lib/libobjc.A.dylib")
        self.objc.objc_getClass.argtypes = [C.c_char_p]
        self.objc.objc_getClass.restype = C.c_void_p
        self.objc.sel_registerName.argtypes = [C.c_char_p]
        self.objc.sel_registerName.restype = C.c_void_p
        self.send = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(("objc_msgSend", self.objc))
        self.pool = self.send(self.objc.objc_getClass(b"NSAutoreleasePool"), self.sel(b"alloc"))
        self.pool = self.send(self.pool, self.sel(b"init"))
        self.info = self.send(self.objc.objc_getClass(b"NSProcessInfo"), self.sel(b"processInfo"))
        string = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p)(("objc_msgSend", self.objc))
        reason = string(self.objc.objc_getClass(b"NSString"), self.sel(b"stringWithUTF8String:"), b"Lecture locale des dialogues")
        begin = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint64, C.c_void_p)(("objc_msgSend", self.objc))
        # NSProcessInfo.h: UserInitiated & ~IdleSystemSleepDisabled.
        self.token = begin(self.info, self.sel(b"beginActivityWithOptions:reason:"), 0x00FFFFFF & ~(1 << 20), reason)
        self.send(self.token, self.sel(b"retain"))
        return self

    def sel(self, value):
        return self.objc.sel_registerName(value)

    def __exit__(self, *_):
        end = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_void_p)(("objc_msgSend", self.objc))
        end(self.info, self.sel(b"endActivity:"), self.token)
        self.send(self.token, self.sel(b"release"))
        self.send(self.pool, self.sel(b"drain"))
