import logging
from datetime import datetime, timedelta, tzinfo

import discord
from discord import Interaction, app_commands
from discord.ext import commands
import pytz
import tabulate

from common import dataio, bankio
from common.utils import interface, pretty

logger = logging.getLogger(f'MAGI.{__name__.split(".")[-1]}')

class Source(commands.Cog):
    """Gestion centralisée de l'économie du serveur"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        default_settings = {
            'DailyAmount': 100, # Crédits quotidiens
            'DailyMsgThreshold': 10, # Nombre de messages nécessaires par jour pour obtenir les crédits quotidiens
            'DailyWealthLimit': 1000 # Limite de richesse pour obtenir les crédits quotidiens
        }
        self.data.append_collection_initializer_for(discord.Guild, 'settings', default_values=default_settings)
        
        # Tracking des crédits quotidiens
        tracking = dataio.TableInitializer(
            table_name='tracking',
            create_query="""CREATE TABLE IF NOT EXISTS tracking (
                user_id INTEGER PRIMARY KEY,
                last_daily TEXT,
                daily_count INTEGER DEFAULT 0
                )"""
        )
        self.data.append_initializers_for(discord.Guild, [tracking])
        
        self.show_user_account = app_commands.ContextMenu(
            name='Compte bancaire',
            callback=self.user_account_callback,
            extras={'description': "Affiche le compte bancaire du membre visé"})
        self.bot.tree.add_command(self.show_user_account)
        
    def cog_unload(self):
        self.data.close_all()
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Ajoute les crédits quotidiens si nécessaire"""
        guild = message.guild
        if not guild or not isinstance(message.author, discord.Member):
            return
        if self.get_daily_amount(guild) == 0:
            return
        if message.author.bot:
            return
        threshold = self.get_daily_msg_threshold(guild)
        daily_count = self.get_daily_tracking(message.author)
        daily_count += 1
        if daily_count < threshold:
            self.set_daily_tracking(message.author, daily_count)
        elif daily_count == threshold:
            self.set_daily_tracking(message.author, daily_count)
            account = bankio.get_account(message.author)
            if account.balance > self.get_daily_wealth_limit(guild):
                return
            account.deposit(self.get_daily_amount(guild), reason="Aide quotidienne")
        
    # Tracking des crédits quotidiens -------------------------------------------
    
    def get_daily_amount(self, guild: discord.Guild) -> int:
        """Renvoie le montant de crédits quotidiens pour un serveur"""
        return self.data.get_collection_value(guild, 'settings', 'DailyAmount', cast=int)
    
    def get_daily_msg_threshold(self, guild: discord.Guild) -> int:
        """Renvoie le nombre de messages nécessaires pour obtenir les crédits quotidiens"""
        return self.data.get_collection_value(guild, 'settings', 'DailyMsgThreshold', cast=int)
    
    def get_daily_wealth_limit(self, guild: discord.Guild) -> int:
        """Renvoie la limite de richesse pour obtenir les crédits quotidiens"""
        return self.data.get_collection_value(guild, 'settings', 'DailyWealthLimit', cast=int)
    
    def get_daily_tracking(self, user: discord.Member) -> int:
        """Renvoie la date du dernier crédit quotidien et le nombre de messages envoyés depuis"""
        today = datetime.now(self.get_timezone(user.guild)).strftime('%Y-%m-%d')
        r = self.data.get(user.guild).fetchone("SELECT last_daily, daily_count FROM tracking WHERE user_id = ?", (user.id,))
        if r is None:
            return 0
        if r['last_daily'] != today:
            return 0
        return r['daily_count']
    
    def set_daily_tracking(self, user: discord.Member, daily_count: int):
        """Met à jour le tracking des crédits quotidiens"""
        today = datetime.now(self.get_timezone(user.guild)).strftime('%Y-%m-%d')
        self.data.get(user.guild).execute("INSERT OR REPLACE INTO tracking VALUES (?, ?, ?)", (user.id, today, daily_count))
        
    def get_timezone(self, guild: discord.Guild | None = None) -> tzinfo:
        if not guild:
            return pytz.timezone('Europe/Paris')
        core : Core = self.bot.get_cog('Core') # type: ignore
        if not core:
            return pytz.timezone('Europe/Paris')
        tz = core.get_guild_global_setting(guild, 'Timezone')
        return pytz.timezone(tz) 
        
    # Comptes bancaires ------------------------------------------------------
    
    def get_account_embed(self, account: bankio.BankAccount) -> discord.Embed:
        """Renvoie un embed décrivant un compte bancaire"""
        yesterday = datetime.now() - timedelta(days=1)
        bank = bankio.get_bank(account.owner.guild) 
        
        embed = discord.Embed(title=f"**Compte bancaire** · *{account.owner.display_name}*", color=account.owner.color)
        embed.set_thumbnail(url=account.owner.display_avatar.url)
        embed.add_field(name="Solde", value=pretty.codeblock(f"{account.balance} {bankio.CURRENCY_SYMBOL}", lang='css'))
        embed.add_field(name="Var/24h", value=pretty.codeblock(f"{account.get_balance_variation(start=yesterday):+}", lang='diff'))
        rank = bank.get_account_rank(account)
        if rank is not None:
            rank = bank.accounts_count
        embed.add_field(name="Rang", value=pretty.codeblock(f"{rank}" + ('e' if rank > 1 else 'er')))
        
        logs = account.fetch_logs(limit=5)
        if logs:
            table = []
            for log in logs:
                table.append([f"{log.amount:+}", pretty.shorten_text(log.reason, 30)])
            embed.add_field(name="Dernières transactions", value=pretty.codeblock(tabulate.tabulate(table, tablefmt='plain', numalign='left'), lang='diff'), inline=False)
        return embed
    
    def get_logs_pages(self, account: bankio.BankAccount) -> list[discord.Embed]:
        """Renvoie une liste d'embeds décrivant les logs d'un compte bancaire"""
        pages = []
        logs = sorted(account.logs, key=lambda log: log.timestamp, reverse=True)
        today_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        text = ""
        for log in logs:
            if log.timestamp.timestamp() > today_midnight.timestamp():
                text += f"<t:{int(log.timestamp.timestamp())}:t> `{log.amount:+}` *{pretty.shorten_text(log.reason, 50)}*\n"
            else:
                text += f"<t:{int(log.timestamp.timestamp())}:f> `{log.amount:+}` *{pretty.shorten_text(log.reason, 50)}*\n"
            if len(text) > 2000:
                pages.append(discord.Embed(title=f"**Logs bancaires** · *{account.owner.display_name}*", description=text, color=account.owner.color))
                text = ""
        if text:
            pages.append(discord.Embed(title=f"**Logs bancaires** · *{account.owner.display_name}*", description=text, color=account.owner.color))
        for p in pages:
            p.set_footer(icon_url=account.owner.display_avatar.url, text=f"Logs des 30 derniers jours · Page {pages.index(p)+1}/{len(pages)}")
        return pages
    
    def get_leaderboard_embed(self, guild: discord.Guild, limit: int = 10) -> discord.Embed:
        """Renvoie un embed décrivant le classement des comptes bancaires"""
        bank = bankio.get_bank(guild)
        lb = bank.get_leaderboard(limit=limit)
        table = []
        for i, account in enumerate(lb):
            table.append([i+1, account.owner.display_name, account.balance])
        embed = discord.Embed(title=f"**Top des plus riches** · *{guild.name}*", description=pretty.codeblock(tabulate.tabulate(table, headers=('#', 'Membre', 'Solde')), lang='css'), color=pretty.DEFAULT_EMBED_COLOR)
        text = f"Nb. de comptes · `{bank.accounts_count}`\n"
        text += f"Total crédits · `{bank.total_balance} {bankio.CURRENCY_SYMBOL}`\n"
        text += f"Moyenne des soldes · `{bank.average_balance} {bankio.CURRENCY_SYMBOL}`\n"
        text += f"Médiane des soldes · `{bank.median_balance} {bankio.CURRENCY_SYMBOL}`\n"
        embed.add_field(name="Statistiques", value=text)
        return embed
        
    # COMMANDES ================================================================

    async def user_account_callback(self, interaction: Interaction, user: discord.Member):
        """Affiche le compte bancaire d'un membre"""
        account = bankio.get_account(user)
        await interaction.response.send_message(embed=self.get_account_embed(account), ephemeral=True)
        
    @app_commands.command(name='account')
    @app_commands.guild_only()
    @app_commands.rename(user='membre')
    async def show_account(self, interaction: Interaction, user: discord.Member | None = None):
        """Affiche le compte bancaire d'un membre

        :param user: Membre dont on veut afficher le compte bancaire"""
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés.", ephemeral=True)
        if user is None:
            user = interaction.user
        account = bankio.get_account(user)
        await interaction.response.send_message(embed=self.get_account_embed(account))
        
    @app_commands.command(name='logs')
    @app_commands.guild_only()
    @app_commands.rename(user='membre')
    async def show_logs(self, interaction: Interaction, user: discord.Member | None = None):
        """Affiche les logs d'un compte bancaire

        :param user: Membre dont on veut afficher les logs"""
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés.", ephemeral=True)
        if user is None:
            user = interaction.user
        account = bankio.get_account(user)
        pages = self.get_logs_pages(account)
        if not pages:
            return await interaction.response.send_message(f"**Logs bancaires** · Aucune transaction n'a été effectuée sur le compte de {user.mention}.", ephemeral=True)
        if len(pages) == 1:
            await interaction.response.send_message(embed=pages[0])
        else:
            view = interface.EmbedPaginatorMenu(embeds=pages, users=[interaction.user])
            await view.start(interaction)
            
    @app_commands.command(name='transfer')
    @app_commands.guild_only()
    @app_commands.rename(user='membre', amount='montant', reason='raison')
    async def transfer(self, interaction: Interaction, user: discord.Member, amount: app_commands.Range[int, 1], reason: str | None = None):
        """Transfère des crédits d'un compte à un autre

        :param user: Membre à qui on veut transférer des crédits
        :param amount: Montant à transférer
        :param reason: Raison du transfert"""
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés.", ephemeral=True)
        if interaction.user == user:
            return await interaction.response.send_message("**Membre invalide** · Vous ne pouvez pas vous transférer des crédits.", ephemeral=True)
        account = bankio.get_account(interaction.user)
        if account.balance < amount:
            return await interaction.response.send_message("**Solde insuffisant** · Vous n'avez pas assez de crédits pour effectuer ce transfert.", ephemeral=True)
        withdraw, deposit = account.transfer(user, amount, reason=f"Transfert ({reason or 'N/A'})")
        withdraw.update_metadata(transfer_to=deposit.account.owner.id)
        deposit.update_metadata(transfer_from=withdraw.account.owner.id)
        if reason:
            return await interaction.response.send_message(f"**Transfert effectué** · {amount} crédits ont été transférés à {user.mention} pour la raison suivante : `{reason}`", silent=True)
        await interaction.response.send_message(f"**Transfert effectué** · {amount} crédits ont été transférés à {user.mention}.", silent=True)
        
    @app_commands.command(name='leaderboard')
    @app_commands.guild_only()
    @app_commands.rename(limit='nombre')
    async def leaderboard(self, interaction: Interaction, limit: app_commands.Range[int, 1, 30] = 10):  
        """Affiche le classement des comptes bancaires
        
        :param limit: Nombre de comptes à afficher"""
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés.", ephemeral=True)
        await interaction.response.send_message(embed=self.get_leaderboard_embed(interaction.guild, limit=limit))
        
    # COMMANDES ADMIN ==========================================================
    
    bank_group = app_commands.Group(name='bank', description="Gestion de l'économie du serveur", guild_only=True, default_permissions=discord.Permissions(moderate_members=True))
    
    @bank_group.command(name='setbalance')
    @app_commands.rename(user='membre', amount='montant', reason='raison')
    async def set_balance(self, interaction: Interaction, user: discord.Member, amount: int, reason: str | None = None):
        """Modifie le solde d'un compte bancaire

        :param user: Membre dont on veut modifier le solde
        :param amount: Nouveau solde
        :param reason: Raison de la modification"""
        account = bankio.get_account(user)
        account.set_balance(amount, reason=reason or "Modification manuelle du solde")
        if reason:
            return await interaction.response.send_message(f"**Solde modifié** · Le nouveau solde de {user.mention} est de `{amount} {bankio.CURRENCY_SYMBOL}` pour la raison suivante : `{reason}`", silent=True)
        await interaction.response.send_message(f"**Solde modifié** · Le nouveau solde de {user.mention} est de `{amount} {bankio.CURRENCY_SYMBOL}`.", silent=True)
    
    daily_subgroup = app_commands.Group(name='daily', description="Paramétrage des crédits quotidiens", guild_only=True, default_permissions=discord.Permissions(moderate_members=True), parent=bank_group)
    
    @daily_subgroup.command(name='amount')
    @app_commands.rename(amount='montant')
    async def set_daily_amount(self, interaction: Interaction, amount: app_commands.Range[int, 0]):
        """Modifie le montant des crédits quotidiens

        :param amount: Nouveau montant (0 pour désactiver)"""
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés.", ephemeral=True)
        self.data.set_keyvalue_table_value(interaction.guild, 'settings', 'DailyAmount', amount)
        if amount == 0:
            await interaction.response.send_message(f"**Montant modifié** · Les crédits quotidiens sont désactivés.", silent=True)
        await interaction.response.send_message(f"**Montant modifié** · Le nouveau montant des crédits quotidiens est de `{amount} {bankio.CURRENCY_SYMBOL}`.", silent=True)
        
    @daily_subgroup.command(name='threshold')
    @app_commands.rename(threshold='seuil')
    async def set_daily_msg_threshold(self, interaction: Interaction, threshold: app_commands.Range[int, 1]):
        """Modifie le nombre de messages nécessaires pour obtenir les crédits quotidiens

        :param threshold: Nouveau nombre de messages"""
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés.", ephemeral=True)
        self.data.set_keyvalue_table_value(interaction.guild, 'settings', 'DailyMsgThreshold', threshold)
        await interaction.response.send_message(f"**Nombre de messages modifié** · Le nouveau nombre de messages nécessaires pour obtenir les crédits quotidiens est de `{threshold}`.", silent=True)

    @daily_subgroup.command(name='wealthlimit')
    @app_commands.rename(limit='limite')
    async def set_daily_wealth_limit(self, interaction: Interaction, limit: app_commands.Range[int, 1]):
        """Modifie la limite de richesse pour obtenir les crédits quotidiens

        :param limit: Nouvelle limite"""
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("Cette commande n'est pas disponible en messages privés.", ephemeral=True)
        self.data.set_keyvalue_table_value(interaction.guild, 'settings', 'DailyWealthLimit', limit)
        await interaction.response.send_message(f"**Limite modifiée** · La nouvelle limite de richesse pour obtenir les crédits quotidiens est de `{limit} {bankio.CURRENCY_SYMBOL}`.", silent=True)

async def setup(bot):
    await bot.add_cog(Source(bot))

