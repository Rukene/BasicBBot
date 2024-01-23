# Script central de l'économie du bot

from collections import namedtuple
from datetime import datetime
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, NamedTuple

import discord
from discord.ext import commands

__BANKS: dict[int, 'GuildBank'] = {}
CURRENCY_SYMBOL = '✽'
COMMON_DB_PATH = Path('common/global')

# ERREURS ---------------------------------------------------------------------

class BankError(commands.CommandError):
    """Erreur de la banque"""
    pass

class AccountError(BankError):
    """Erreur de compte"""
    pass

class AccountBalanceError(AccountError):
    """Erreur de solde"""
    pass

class AccountInsufficientBalanceError(AccountBalanceError):
    """Erreur de solde insuffisant"""
    pass

class AccountNegativeBalanceError(AccountBalanceError):
    """Erreur de solde négatif"""
    pass

class LogError(BankError):
    """Erreur de log"""
    pass

class LogNotFoundError(LogError):
    """Erreur de log"""
    pass

class LogMetadataError(LogError):
    """Erreur de métadonnées"""
    pass    

# CLASSES ---------------------------------------------------------------------

class GuildBank:
    def __init__(self, guild: discord.Guild):
        """Représente la banque d'un serveur

        :param guild: Serveur de la banque
        """
        self.guild = guild
        self.db_path = COMMON_DB_PATH / f'bank_{guild.id}.db'
        
        self._conn = self.__connect()
        self.__initialize(self._conn)
        
        self.__accounts : dict[int, 'BankAccount'] = {}
        
    def __repr__(self):
        return f'<GuildBank guild={self.guild}>'
    
    def __str__(self):
        return f'Banque de {self.guild.name}'
    
    # Base de données ------------
    
    def __connect(self):
        if not COMMON_DB_PATH.exists():
            COMMON_DB_PATH.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def __initialize(self, conn: sqlite3.Connection):
        with closing(conn.cursor()) as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                metadata TEXT DEFAULT '',
                FOREIGN KEY (account_id) REFERENCES accounts (user_id)
            )""")
            conn.commit()
    
    # Comptes --------------------
    
    def _get_bank_account(self, user: discord.Member) -> 'BankAccount':
        if user.id not in self.__accounts:
            self.__accounts[user.id] = BankAccount(user)
        return self.__accounts[user.id]
    
    def _get_bank_accounts(self, *, predicate: Callable[['BankAccount'], bool] = lambda _: True) -> list['BankAccount']:
        members = {member.id: member for member in self.guild.members}
        with closing(self._conn.cursor()) as cur:
            cur.execute('SELECT user_id FROM accounts')
            accounts = [self._get_bank_account(members[row['user_id']]) for row in cur.fetchall()]
        if predicate is not None:
            accounts = list(filter(predicate, accounts))
        return accounts
    
    # Logs -----------------------
    
    def _get_bank_log(self, id: int) -> 'BankLog':
        with closing(self._conn.cursor()) as cur:
            cur.execute('SELECT * FROM logs WHERE id = ?', (id,))
            row = cur.fetchone()
            if row is None:
                raise LogNotFoundError('Log introuvable')
            member = self.guild.get_member(row['account_id'])
            if member is None:
                raise LogNotFoundError('Log introuvable')
            account = self._get_bank_account(member)
            return BankLog(account, row['id'], row['amount'], datetime.fromtimestamp(row['timestamp']), **json.loads(row['metadata']))
        
    def _get_bank_logs(self, *, predicate: Callable[['BankLog'], bool] = lambda _: True) -> list['BankLog']:
        members = {member.id: member for member in self.guild.members}
        with closing(self._conn.cursor()) as cur:
            cur.execute('SELECT * FROM logs')
            logs = [BankLog(self._get_bank_account(members[row['account_id']]), row['id'], row['amount'], datetime.fromtimestamp(row['timestamp']), **json.loads(row['metadata'])) for row in cur.fetchall()]
        if predicate is not None:
            logs = list(filter(predicate, logs))
        return logs
    
    # Leaderboard & Stats --------
    
    def get_leaderboard(self, *, limit: int = 10) -> list['BankAccount']:
        """Renvoie le classement des comptes de la banque
        
        :param limit: Nombre de comptes à renvoyer"""
        return sorted(self._get_bank_accounts(), key=lambda a: a.balance, reverse=True)[:limit]
    
    def get_account_rank(self, account: 'BankAccount') -> int:
        """Renvoie le rang d'un compte dans la banque
        
        :param account: Compte à classer"""
        lb = sorted(self._get_bank_accounts(), key=lambda a: a.balance, reverse=True)
        return lb.index(account) + 1 if account in lb else self.accounts_count
    
    @property
    def accounts_count(self) -> int:
        """Renvoie le nombre de comptes dans la banque"""
        return len(self._get_bank_accounts())
    
    @property
    def total_balance(self) -> int:
        """Renvoie le solde total de la banque"""
        return sum(account.balance for account in self._get_bank_accounts())
    
    @property
    def average_balance(self) -> float:
        """Renvoie le solde moyen des comptes de la banque"""
        return round(self.total_balance / self.accounts_count, 2) if self.accounts_count > 0 else 0
    
    @property
    def median_balance(self) -> float | int:
        """Renvoie le solde médian des comptes de la banque"""
        accounts = sorted(self._get_bank_accounts(), key=lambda a: a.balance)
        if len(accounts) % 2 == 0:
            return (accounts[len(accounts) // 2 - 1].balance + accounts[len(accounts) // 2].balance) / 2
        return accounts[len(accounts) // 2].balance
    
            
class BankAccount:
    def __init__(self, user: discord.Member):
        """Représente le compte d'un utilisateur

        :param user: Utilisateur du compte
        """
        self.owner = user
        self.bank : GuildBank = get_bank(user.guild)
        self.__balance : int = self.__load_balance()
        self.__logs : list['BankLog'] = self.__load_logs()
        
    def __repr__(self):
        return f'<BankAccount user={self.owner}>'
    
    def __str__(self):
        return f'{self.owner.display_name}'
    
    def __eq__(self, other):
        if isinstance(other, BankAccount):
            return self.owner == other.owner and self.balance == other.balance
        return NotImplemented
            
    # Solde -----------------
    
    def __load_balance(self) -> int:
        with closing(self.bank._conn.cursor()) as cur:
            cur.execute('SELECT balance FROM accounts WHERE user_id = ?', (self.owner.id,))
            row = cur.fetchone()
            if row is None:
                cur.execute('INSERT INTO accounts (user_id, balance) VALUES (?, ?)', (self.owner.id, 0))
                self.bank._conn.commit()
                return 0
            return row['balance']
        
    def __set_balance(self, value: int):
        with closing(self.bank._conn.cursor()) as cur:
            cur.execute('INSERT OR REPLACE INTO accounts (user_id, balance) VALUES (?, ?)', (self.owner.id, value))
            self.bank._conn.commit()
            
    @property
    def balance(self) -> int:
        """Solde du compte"""
        return self.__balance
    
    def set_balance(self, value: int, *, reason: str = '', **metadata) -> 'BankLog':
        """Modifie le solde du compte
        
        :param value: Nouveau solde
        :param reason: Raison de la modification
        :param metadata: Métadonnées de la modification
        :return: BankLog de l'opération"""
        if value < 0:
            raise AccountNegativeBalanceError('Le solde ne peut pas être négatif')
        if type(value) is not int:
            value = int(value)
        delta = value - self.balance
        self.__balance = value
        self.__set_balance(value)
        if reason == '':
            return BankLog.create(self, delta, **metadata)
        return BankLog.create(self, delta, reason=reason, **metadata)

    def deposit(self, amount: int, *, reason: str = '', **metadata) -> 'BankLog':
        """Dépose de l'argent sur le compte
        
        :param amount: Montant à déposer
        :param reason: Raison du dépôt
        :param metadata: Métadonnées du dépôt
        :return: BankLog de l'opération"""
        return self.set_balance(self.balance + amount, reason=reason, **metadata)
        
    def withdraw(self, amount: int, *, reason: str = '', **metadata) -> 'BankLog':
        """Retire de l'argent du compte
        
        :param amount: Montant à retirer
        :param reason: Raison du retrait
        :param metadata: Métadonnées du retrait
        :return: BankLog de l'opération"""
        if amount > self.balance:
            raise AccountInsufficientBalanceError('Solde insuffisant')
        return self.set_balance(self.balance - amount, reason=reason, **metadata) 
        
    def rollback(self, log: 'BankLog | int') -> 'BankLog':
        """Annule l'opération liée à un log
        
        :param log: Log à annuler
        :return: BankLog de l'opération"""
        if isinstance(log, BankLog):
            log = log.id
        l = self.fetch_log(log)
        if l is None:
            raise LogNotFoundError('Log introuvable')
        if l.account != self:
            raise LogError('Le log ne correspond pas au compte')
        l.metadata['rollback'] = True
        return self.set_balance(self.balance - l.amount, reason='Annulation', **l.metadata)
        
    # Logs ------------------
    
    def __load_logs(self) -> list['BankLog']:
        with closing(self.bank._conn.cursor()) as cur:
            cur.execute('SELECT * FROM logs WHERE account_id = ?', (self.owner.id,))
            logs = [BankLog(self, row['id'], row['amount'], datetime.fromtimestamp(row['timestamp']), **json.loads(row['metadata'])) for row in cur.fetchall()]
            return sorted(logs, key=lambda l: l.timestamp)
    
    @property
    def logs(self) -> list['BankLog']:
        """Logs bancaires du compte"""
        return self.__logs
    
    def fetch_log(self, id: int) -> 'BankLog | None':
        """Renvoie un log à partir de son identifiant
        
        :param id: Identifiant du log"""
        for log in self.__logs:
            if log.id == id:
                return log
        return None
    
    def fetch_logs(self, *, limit: int = 10, predicate: Callable[['BankLog'], bool] = lambda _: True) -> list['BankLog']:
        """Renvoie une liste de logs
        
        :param limit: Nombre de logs à renvoyer
        :param predicate: Prédicat de sélection des logs"""
        logs = sorted(self.__logs, key=lambda l: l.timestamp, reverse=True)
        if predicate is not None:
            logs = list(filter(predicate, logs))
        return logs[:limit]
    
    # Opérations spéciales --
    
    def transfer(self, other: 'BankAccount | discord.Member', amount: int, *, reason: str = '', **metadata) -> tuple['BankLog', 'BankLog']:
        """Transfère de l'argent d'un compte à un autre
        
        :param other: Compte destinataire
        :param amount: Montant à transférer
        :param reason: Raison du transfert
        :param metadata: Métadonnées du transfert
        :return: BankLog du retrait et BankLog du dépôt"""
        if isinstance(other, discord.Member):   
            other = get_account(other)
        if amount > self.balance:
            raise AccountInsufficientBalanceError('Solde insuffisant')
        return self.withdraw(amount, reason=reason, **metadata), other.deposit(amount, reason=reason, **metadata)
    
    # Utilitaires ------------
    
    def get_balance_variation(self, *, start: datetime, end: datetime | None = None) -> int:
        """Renvoie la variation du solde du compte dans une période donnée
        
        :param start: Date de début de la période
        :param end: Date de fin de la période, si None alors datetime.now()"""
        if end is None:
            end = datetime.now()
        return sum(log.amount for log in self.logs if start <= log.timestamp <= end)
    
        
class BankLog:
    def __init__(self, account: BankAccount, id: int, amount: int, timestamp: datetime, **metadata):
        """Représente un log bancaire

        :param account: Compte de l'opération
        :param id: Identifiant unique du log
        :param amount: Montant de l'opération
        :param timestamp: Date de l'opération
        :param metadata: Métadonnées de l'opération
        """
        self.account = account
        self.id = id
        self.amount = amount
        self.timestamp = timestamp
        self.metadata : dict[str, Any] = metadata
        
    def __repr__(self):
        return f'<BankLog account={self.account} id={self.id} amount={self.amount} timestamp={self.timestamp}>'
    
    def __eq__(self, other):
        if isinstance(other, BankLog):
            return self.id == other.id and self.account == other.account
        return NotImplemented 
    
    # Création ----------------
    
    @classmethod
    def create(cls, account: BankAccount, amount: int, **metadata) -> 'BankLog':
        try:
            json.dumps(metadata)
        except TypeError:
            raise LogMetadataError('Les métadonnées doivent être sérialisables en JSON')
        
        with closing(account.bank._conn.cursor()) as cur:
            cur.execute('INSERT INTO logs (account_id, amount, timestamp, metadata) VALUES (?, ?, ?, ?) RETURNING id', (account.owner.id, amount, datetime.now().timestamp(), json.dumps(metadata)))
            log_id = cur.fetchone()[0]
            account.bank._conn.commit()
            new_log = cls(account, log_id, amount, datetime.now(), **metadata)
            account.logs.append(new_log)
        return new_log
    
    # Edition -----------------
    
    def update_metadata(self, **metadata):
        """Met à jour les métadonnées du log en fusionnant les nouvelles données avec les anciennes"""
        current = self.metadata
        current.update(metadata)
        with closing(self.account.bank._conn.cursor()) as cur:
            cur.execute('UPDATE logs SET metadata = ? WHERE id = ?', (json.dumps(current), self.id))
            self.account.bank._conn.commit()
        self.metadata = current
        
    def replace_metadata(self, **metadata):
        """Met à jour les métadonnées du log en remplaçant entièrement les anciennes données par les nouvelles"""
        with closing(self.account.bank._conn.cursor()) as cur:
            cur.execute('UPDATE logs SET metadata = ? WHERE id = ?', (json.dumps(metadata), self.id))
            self.account.bank._conn.commit()
        self.metadata = metadata
        
    # Suppression --------------
    
    def delete(self):
        with closing(self.account.bank._conn.cursor()) as cur:
            cur.execute('DELETE FROM logs WHERE id = ?', (self.id,))
            self.account.bank._conn.commit()
            
    # Propriétés ---------------
    
    @property
    def reason(self) -> str:
        """Renovie la raison de l'opération"""
        return self.metadata.get('reason', 'N/A')
    
# FONCTIONS -------------------------------------------------------------------

# Banque 
def get_bank(guild: discord.Guild) -> GuildBank:
    """Renvoie la banque d'une guilde
    
    :param guild: Serveur de la banque"""
    if guild.id not in __BANKS:
        __BANKS[guild.id] = GuildBank(guild)
    return __BANKS[guild.id]

# Comptes
def get_account(user: discord.Member) -> BankAccount:
    """Renvoie le compte d'un utilisateur
    
    :param user: Utilisateur du compte"""
    return get_bank(user.guild)._get_bank_account(user)

def get_accounts(guild: discord.Guild, *, predicate: Callable[['BankAccount'], bool] = lambda _: True) -> list[BankAccount]:
    """Renvoie une liste de comptes
    
    :param guild: Serveur de la banque
    :param predicate: Prédicat de sélection des comptes"""
    return get_bank(guild)._get_bank_accounts(predicate=predicate)

# Logs  
def get_bank_log(guild: discord.Guild, id: int) -> BankLog:
    """Renvoie un log à partir de son identifiant
    
    :param guild: Serveur de la banque
    :param id: Identifiant du log"""
    return get_bank(guild)._get_bank_log(id)

def get_bank_logs(guild: discord.Guild, *, predicate: Callable[['BankLog'], bool] = lambda _: True) -> list[BankLog]:
    """Renvoie une liste de logs
    
    :param guild: Serveur de la banque
    :param predicate: Prédicat de sélection des logs"""
    return get_bank(guild)._get_bank_logs(predicate=predicate)
