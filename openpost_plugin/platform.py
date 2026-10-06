"""Native macOS facilities. Secrets never become command-line arguments."""
import ctypes
import subprocess
import sys


class PlatformError(Exception):
    pass


class Keychain:
    SERVICE = b"com.locus.plugin.social-studio"

    def __init__(self):
        if sys.platform != "darwin":
            raise PlatformError("OpenPost credentials require macOS Keychain.")
        self.sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ptr, uint = ctypes.c_void_p, ctypes.c_uint32
        self.sec.SecKeychainAddGenericPassword.argtypes = [ptr, uint, ptr, uint, ptr, uint, ptr, ctypes.POINTER(ptr)]
        self.sec.SecKeychainFindGenericPassword.argtypes = [ptr, uint, ptr, uint, ptr, ctypes.POINTER(uint), ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
        self.sec.SecKeychainItemFreeContent.argtypes = [ptr, ptr]
        self.sec.SecKeychainItemDelete.argtypes = [ptr]
        self.cf.CFRelease.argtypes = [ptr]

    def save(self, identifier, token):
        account, data = identifier.encode(), token.encode()
        status = self.sec.SecKeychainAddGenericPassword(None, len(self.SERVICE), self.SERVICE, len(account), account, len(data), data, None)
        if status:
            raise PlatformError("Couldn't save the OpenPost token in macOS Keychain.")

    def _find(self, identifier):
        account = identifier.encode()
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status = self.sec.SecKeychainFindGenericPassword(None, len(self.SERVICE), self.SERVICE, len(account), account, ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
        if status == -25300:
            return None, None
        if status:
            raise PlatformError("Couldn't read macOS Keychain. Unlock it and reconnect OpenPost.")
        try:
            return ctypes.string_at(data, length.value).decode(), item
        finally:
            self.sec.SecKeychainItemFreeContent(None, data)

    def load(self, identifier):
        token, item = self._find(identifier)
        if item:
            self.cf.CFRelease(item)
        return token

    def delete(self, identifier):
        _, item = self._find(identifier)
        if item:
            try:
                if self.sec.SecKeychainItemDelete(item):
                    raise PlatformError("Couldn't remove the OpenPost token from macOS Keychain.")
            finally:
                self.cf.CFRelease(item)


class MacOS:
    @staticmethod
    def _script(script, cancelled):
        # The only script argument is fixed source. Prompt answers return over a
        # private pipe; they are never placed in argv, a shell, or tool output.
        process = subprocess.Popen(["/usr/bin/osascript", "-e", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while True:
            if cancelled.is_set():
                process.terminate()
                process.communicate()
                raise PlatformError("The panel request was cancelled.")
            try:
                output, _ = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise PlatformError("The native dialog was cancelled or unavailable.")
        return output.rstrip("\n")

    def prompt_token(self, cancelled):
        return self._script('text returned of (display dialog "Paste your OpenPost developer token. It will be stored in macOS Keychain." with title "Connect OpenPost" default answer "" with hidden answer buttons {"Cancel", "Continue"} default button "Continue" cancel button "Cancel")', cancelled)

    def export_path(self, cancelled):
        return self._script('POSIX path of (choose file name with prompt "Export Social Studio drafts" default name "Social Studio drafts.json")', cancelled)

    def copy(self, text):
        result = subprocess.run(["/usr/bin/pbcopy"], input=text.encode(), capture_output=True)
        if result.returncode:
            raise PlatformError("Couldn't copy the draft to the clipboard.")

    def open(self, url):
        result = subprocess.run(["/usr/bin/open", url], capture_output=True)
        if result.returncode:
            raise PlatformError("Couldn't open the browser.")
