"""
### Gestion centralisée des données des modules (cogs) de bot.
Pour l'utiliser, utiliser `get_instance(cog)` pour récupérer l'instance de gestion des données du module `cog`.
"""

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Sequence

import discord
from discord.ext import commands

RESOURCES_PATH = Path('common/resources')
__INSTANCES : dict[str, 'CogData'] = {}

# DONNEES DE COG ===============================================

class CogData:
    def __init__(self, cog_name: str):
        """Classe de gestion des données d'un module.

        :param cog: Module (Cog) lié aux données
        """
        self.cog_name = cog_name
        self.cog_folder = Path(f'cogs/{cog_name}')
        if not self.cog_folder.exists():
            self.cog_folder.mkdir(parents=True, exist_ok=True)
        
        self.__managers : dict[discord.abc.Snowflake | str, ModelDataManager] = {}
        self.__defaults : dict[type[discord.abc.Snowflake] | str, tuple[TableDefault, ...]] = {}
        
    def __repr__(self) -> str:
        return f'<CogDataInstance cog_name={self.cog_name!r}>'
    
    # --- Connexions ---
    
    def __model_db_name(self, model: discord.abc.Snowflake | str) -> str:
        if isinstance(model, discord.abc.Snowflake):
            return f'{model.__class__.__name__}_{model.id}'.lower()
        elif isinstance(model, str):
            return re.sub(r'[^a-z0-9_]', '_', model.lower())
        else:
            raise TypeError(f'Invalid model type: {type(model)}')
    
    def __get_manager(self, model: discord.abc.Snowflake | str) -> 'ModelDataManager':
        db_name = self.__model_db_name(model)
        defaults = self.get_defaults(type(model) if isinstance(model, discord.abc.Snowflake) else model)
        return ModelDataManager(model, self.cog_folder / 'data' / f'{db_name}.db', defaults=defaults)
    
    # --- Dossiers ---
    
    def get_subfolder(self, name: str, *, create: bool = False) -> Path:
        """Renvoie le chemin du dossier `name` du module.

        :param name: Nom du dossier
        :param create: Si `True`, crée le dossier s'il n'existe pas
        :return: Chemin du dossier
        """
        folder = self.cog_folder / name
        if create:
            folder.mkdir(exist_ok=True)
        return folder
    
    @property
    def assets_path(self) -> Path:
        """Renvoie le chemin du dossier assets du module."""
        return self.get_subfolder('assets')
    
    # --- Modèles ---
    
    def get(self, model: discord.abc.Snowflake | str) -> 'ModelDataManager':
        """Renvoie le gestionnaire de données du modèle spécifié.

        :param model: Modèle (discord.Guild, discord.User, ...) lié aux données
        :return: Gestionnaire de données
        """
        if isinstance(model, str):
            model = model.lower()
        if model not in self.__managers:
            self.__managers[model] = self.__get_manager(model)
        return self.__managers[model]
    
    def get_all(self) -> list['ModelDataManager']:
        """Renvoie tous les gestionnaires de données du module.

        :return: Gestionnaires de données
        """
        return list(self.__managers.values())
    
    def close(self, model: discord.abc.Snowflake | str) -> None:
        """Ferme la connexion à la base de données du modèle spécifié.

        :param model: Modèle (discord.Guild, discord.User, ...) lié aux données
        """
        if isinstance(model, str):
            model = model.lower()
        if model in self.__managers:
            self.__managers[model].close()
            del self.__managers[model]
            
    def close_all(self) -> None:
        """Ferme la connexion à toutes les bases de données du module."""
        for manager in self.__managers.values():
            manager.close()
        self.__managers.clear()
        
    def delete(self, model: discord.abc.Snowflake | str) -> None:
        """Supprime la base de données du modèle spécifié.

        :param model: Modèle (discord.Guild, discord.User, ...) lié aux données
        """
        if isinstance(model, str):
            model = model.lower()
        if model in self.__managers:
            self.__managers[model].close()
            del self.__managers[model]
        db_name = self.__model_db_name(model)
        db_path = self.cog_folder / f'{db_name}.db'
        if db_path.exists():
            db_path.unlink()
            
    def delete_all(self) -> None:
        """Supprime toutes les bases de données du module."""
        for manager in self.__managers.values():
            manager.close()
        self.__managers.clear()
        for db_path in self.cog_folder.glob('*.db'):
            db_path.unlink()
    
    # --- Définitions ---
    
    def set_defaults(self, model_type: type[discord.abc.Snowflake] | str, *defaults: 'TableDefault') -> None:
        """Définit les valeurs par défaut d'un modèle.

        :param model_type: Type du modèle
        :param defaults: Définitions des tables de données
        """
        if isinstance(model_type, str):
            model_type = model_type.lower()
        self.__defaults[model_type] = defaults
        
    def get_defaults(self, model_type: type[discord.abc.Snowflake] | str) -> tuple['TableDefault', ...]:
        """Renvoie les valeurs par défaut d'un modèle.

        :param model_type: Type du modèle
        :return: Définitions des tables de données
        """
        if isinstance(model_type, str):
            model_type = model_type.lower()
        return self.__defaults.get(model_type, ())
   
# MANAGER ===================================================
    
class ModelDataManager:
    """Classe de gestion des données d'un modèle (discord.Guild, discord.User, ...)"""
    def __init__(self, model: discord.abc.Snowflake | str, db_path: Path, *, defaults: Sequence['TableDefault'] = []):
        self.model = model
        self.defaults = defaults
        
        self.conn : sqlite3.Connection = self.__get_connection(db_path)
        
    def __repr__(self) -> str:
        return f'<ModelDataManager model={self.model!r}>'
    
    # --- Propriétés ---
    
    @property
    def tables(self) -> list[str]:
        """Renvoie la liste des tables de la base de données."""
        return [table['name'] for table in self.fetch_all('SELECT name FROM sqlite_master WHERE type="table"')]
    
    # --- Connexions ---
    
    def __get_connection(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        
        # Initialisation des tables (défaults)
        commit_on_close = False
        with closing(conn.cursor()) as cursor:
            tables = cursor.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()
            for default in self.defaults:
                if not default.insert_on_reconnect and default.table_name in tables: # Si la table existe déjà et qu'on ne veut pas réinsérer les valeurs par défaut
                    continue
                cursor.execute(default.query)
                if default.default_values:
                    cursor.executemany(f'INSERT OR IGNORE INTO {default.table_name} ({", ".join(default.default_values[0].keys())}) VALUES ({", ".join(["?" for _ in default.default_values[0]])})', 
                                       [tuple(d.values()) for d in default.default_values])
                commit_on_close = True
        if commit_on_close:
            conn.commit()
        return conn
    
    # --- Tables ---
            
    def execute(self, query: str, *args: Any, commit: bool = True) -> None:
        """Exécute une requête SQL sur la base de données.

        :param query: Requête SQL
        :param args: Arguments de la requête
        :param commit: Si `True`, enregistre les modifications
        """
        with closing(self.conn.cursor()) as cursor:
            cursor.execute(query, *args)
            if commit:
                self.conn.commit()
                
    def execute_many(self, query: str, args: Iterable[Sequence[Any]], *, commit: bool = True) -> None:
        """Exécute un ensemble de requêtes SQL sur la base de données.

        :param query: Requête SQL
        :param args: Arguments de la requête
        :param commit: Si `True`, enregistre les modifications
        """
        with closing(self.conn.cursor()) as cursor:
            cursor.executemany(query, args)
            if commit:
                self.conn.commit()
                
    def fetch(self, query: str, *args: Any) -> dict[str, Any]:
        """Exécute une requête SQL sur la base de données et renvoie le premier résultat.

        :param query: Requête SQL
        :param args: Arguments de la requête
        :return: Résultat de la requête
        """
        with closing(self.conn.cursor()) as cursor:
            cursor.execute(query, *args)
            return cursor.fetchone()
        
    def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Exécute une requête SQL sur la base de données et renvoie tous les résultats.

        :param query: Requête SQL
        :param args: Arguments de la requête
        :return: Résultat de la requête
        """
        with closing(self.conn.cursor()) as cursor:
            cursor.execute(query, *args)
            return cursor.fetchall()
        
    def commit(self) -> None:
        """Enregistre manuellement les modifications sur la base de données."""
        self.conn.commit()
        
    def close(self) -> None:
        """Ferme la connexion à la base de données."""
        self.conn.close()
        
    # --- Utils ---
    
    def fetch_column_names(self, table_name: str) -> list[str]:
        """Renvoie la liste des noms des colonnes de la table spécifiée.

        :param table_name: Nom de la table
        :return: Noms des colonnes
        """
        with closing(self.conn.cursor()) as cursor:
            cursor.execute(f'SELECT * FROM {table_name}')
            return [d[0] for d in cursor.description]
        
    # --- Raccourcis tables clé/valeur ---
    
    def get_dict_value(self, table_name: str, key: str, *, cast: type[Any] = str) -> Any:
        """Renvoie la valeur associée à la clé dans une table clé/valeur.

        :param table_name: Nom de la table
        :param key: Clé
        :param cast: Type de la valeur à renvoyer
        :return: Valeur associée à la clé
        """
        if table_name not in self.tables:
            raise ValueError(f'La table {table_name!r} n\'existe pas')
        columns = self.fetch_column_names(table_name)
        if ('key' not in columns) or ('value' not in columns):
            raise ValueError(f'La table {table_name!r} n\'est pas une table clé/valeur')
        row = self.fetch(f'SELECT * FROM {table_name} WHERE key=?', (key, ))
        if row is None:
            return None
        return cast(row['value'])
    
    def get_dict_values(self, table_name: str) -> dict[str, str]:
        """Renvoie toutes les valeurs de la table clé/valeur spécifiée.

        :param table_name: Nom de la table
        :return: Valeurs de la table
        """
        if table_name not in self.tables:
            raise ValueError(f'La table {table_name!r} n\'existe pas')
        columns = self.fetch_column_names(table_name)
        if ('key' not in columns) or ('value' not in columns):
            raise ValueError(f'La table {table_name!r} n\'est pas une table clé/valeur')
        return {row['key']: str(row['value']) for row in self.fetch_all(f'SELECT * FROM {table_name}')}
    
    def set_dict_value(self, table_name: str, key: str, value: Any) -> None:
        """Définit la valeur associée à la clé dans la table clé/valeur spécifiée.

        :param table_name: Nom de la table
        :param key: Clé 
        :param value: Valeur à associer à la clé (convertie en str)
        """
        if table_name not in self.tables:
            raise ValueError(f'La table {table_name!r} n\'existe pas')
        columns = self.fetch_column_names(table_name)
        if ('key' not in columns) or ('value' not in columns):
            raise ValueError(f'La table {table_name!r} n\'est pas une table clé/valeur')
        try:
            dump = str(value)
        except:
            raise TypeError(f'Impossible de convertir la valeur {value!r} en str')
        self.execute(f'INSERT OR REPLACE INTO {table_name} (key, value) VALUES (?, ?)', (key, dump))
        
    def delete_dict_value(self, table_name: str, key: str) -> None:
        """Supprime la valeur associée à la clé dans la table clé/valeur spécifiée.

        :param table_name: Nom de la table
        :param key: Clé
        """
        if table_name not in self.tables:
            raise ValueError(f'La table {table_name!r} n\'existe pas')
        columns = self.fetch_column_names(table_name)
        if ('key' not in columns) or ('value' not in columns):
            raise ValueError(f'La table {table_name!r} n\'est pas une table clé/valeur')
        self.execute(f'DELETE FROM {table_name} WHERE key=?', (key, ))
        
# DEFAULTS ==================================================

class TableDefault:
    def __init__(self, query: str, default_values: Sequence[dict[str, Any]] = [], *, insert_on_reconnect: bool = False):
        """Classe de définition d'une table de données d'un modèle

        :param query: Requête de création de la table (`CREATE TABLE ...`)
        :param default_values: Valeurs par défaut à insérer dans la table
        :param insert_on_reconnect: Si `True`, les valeurs sont réinsérées à chaque connexion si absentes
        """
        if not query.startswith('CREATE TABLE'):
            raise ValueError('La requête doit commencer par "CREATE TABLE"')
        self.query = query
        
        if default_values:
            keys = set(default_values[0].keys())
            if not all(set(d.keys()) == keys for d in default_values):
                raise ValueError('Les valeurs par défaut doivent avoir les mêmes clés')
        self.default_values = default_values
        self.insert_on_reconnect = insert_on_reconnect
        
    def __repr__(self) -> str:
        return f'<ModelDefault query={self.query!r}>'
    
    @property
    def table_name(self) -> str:
        """Renvoie le nom de la table."""
        r = re.search(r"CREATE TABLE IF NOT EXISTS (.*) \(", self.query)
        if r is None:
            r = re.search(r"CREATE TABLE (.*) \(", self.query)
            
        if r is None:
            raise ValueError('Impossible de trouver le nom de la table')
        return r.group(1)
    
class DictTableDefault(TableDefault): # Pour les tables simplifiées de type clé/valeur
    def __init__(self, name: str, default_values: dict[str, Any] = {}, *, insert_on_reconnect: bool = True):
        """Classe de définition d'une table de données clé/valeur d'un modèle

        :param name: Nom de la table
        :param default_values: Valeurs par défaut à insérer dans la table
        :param force_defaults: Si `True`, les valeurs sont réinsérées à chaque connexion si absentes
        """
        query = f'CREATE TABLE IF NOT EXISTS {name} (key TEXT PRIMARY KEY, value TEXT)'
        if not isinstance(default_values, dict):
            raise TypeError('Les valeurs par défaut doivent être un dictionnaire')
        default = [{'key': k, 'value': v} for k, v in default_values.items()]
        super().__init__(query, default, insert_on_reconnect=insert_on_reconnect)
        
    def __repr__(self) -> str:
        return f'<ModelDictDefault name={self.table_name!r}>'

# INSTANCES =================================================

def get_instance(cog: commands.Cog | str) -> CogData:
    """Renvoie l'instance de gestion des données du module spécifié.

    :param cog: Module (Cog) lié aux données
    :return: Instance de gestion des données
    """
    cog_name = cog.lower() if isinstance(cog, str) else cog.qualified_name.lower()
    if cog_name not in __INSTANCES:
        __INSTANCES[cog_name] = CogData(cog_name)
    return __INSTANCES[cog_name]
