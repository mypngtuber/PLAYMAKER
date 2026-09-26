"""Only the OS credential vault stores secrets."""
import keyring
SERVICE = 'AIShortsMakerPro'

def get(name):
    return keyring.get_password(SERVICE, name)

def set_secret(name, value):
    if value:
        keyring.set_password(SERVICE, name, value)

def delete(name):
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass
