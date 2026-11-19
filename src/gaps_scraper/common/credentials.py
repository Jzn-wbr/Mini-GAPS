"""Identifiants depuis le fichier .env privé, sans interpolation ni journalisation."""

import logging
import os
from pathlib import Path
import stat
from threading import Lock

from dotenv import dotenv_values


class CredentialsError(ValueError):
    pass


_PARSE_LOCK=Lock()


def credentials(path=None):
    path=Path(path) if path is not None else Path(os.environ.get('GAPS_CREDENTIALS_FILE','.env'))
    username,password=os.environ.get('GAPS_USERNAME'),os.environ.get('GAPS_PASSWORD')
    if username or password:
        if not username or not password:
            raise CredentialsError('Les deux variables GAPS_USERNAME et GAPS_PASSWORD sont requises.')
        return username,password
    try:
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise CredentialsError('Le fichier .env doit être privé : chmod 600 .env.')
        # Le parseur peut signaler une ligne invalide : supprimer ces diagnostics,
        # puis produire uniquement une erreur générique, sans aucune valeur reçue.
        with _PARSE_LOCK:
            logger=logging.getLogger('dotenv.main')
            disabled=logger.disabled;logger.disabled=True
            try:data=dotenv_values(path,interpolate=False)
            finally:logger.disabled=disabled
    except (OSError,UnicodeError,ValueError):
        raise CredentialsError('Lecture du fichier .env impossible. Vérifier son format et ses permissions.') from None
    username,password=data.get('GAPS_USERNAME'),data.get('GAPS_PASSWORD')
    if not isinstance(username,str) or not username or not isinstance(password,str) or not password:
        raise CredentialsError('Renseigner GAPS_USERNAME et GAPS_PASSWORD dans .env.')
    return username,password
