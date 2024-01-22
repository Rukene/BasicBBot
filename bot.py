import asyncio
import logging
import os
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import dotenv_values

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s (%(name)s %(module)s) %(message)s",
)
logger = logging.getLogger('MAGI.Main')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

async def main():
    bot = commands.Bot(
        command_prefix="//",
        description="Bot multifonction modulaire français",
        intents=intents,
        help_command=None
    )
    bot.config = dotenv_values('.env') # type: ignore
    
    async with bot:
        print("Chargement des cogs...")
        for folder in os.listdir("./cogs/"):
            ext = folder
            try:
                await bot.load_extension(f"cogs.{ext}.{ext}")
                print(f"- '{ext}'")
            except Exception as e:
                exception = f"{type(e).__name__}: {e}"
                print(f"x Erreur {ext} > {exception}")
        print('--------------')
        
        @bot.event
        async def on_ready():
            print(f"> Connecté en tant que {bot.user}")
            print(f"> Version discord.py : {discord.__version__}")
            print("> Lien d'invitation : {}".format(discord.utils.oauth_url(int(bot.config["APP_ID"]), permissions=discord.Permissions(int(bot.config['PERMISSIONS_INT']))))) # type: ignore
            print(f"> Connecté à {len(bot.guilds)} serveurs :\n" + '\n'.join([f"- {guild.name} ({guild.id})" for guild in bot.guilds]))
            print("--------------")
            
            status = '/help'
            await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=status))
            print(f"> Statut défini sur '{status}'")
    
        @bot.tree.error
        async def on_command_error(interaction: discord.Interaction, error):
            if isinstance(error, app_commands.errors.CommandOnCooldown):
                minutes, seconds = divmod(error.retry_after, 60)
                hours, minutes = divmod(minutes, 60)
                hours = hours % 24
                msg = f"**Cooldown ·** Tu pourras réutiliser la commande dans {f'{round(hours)} heures' if round(hours) > 0 else ''} {f'{round(minutes)} minutes' if round(minutes) > 0 else ''} {f'{round(seconds)} secondes' if round(seconds) > 0 else ''}."
                return await interaction.response.send_message(content=msg, ephemeral=True)
            elif isinstance(error, app_commands.errors.MissingPermissions):
                msg = f"**Erreur ·** Tu manques des permissions `" + ", ".join(error.missing_permissions) + "` pour cette commande !"
                return await interaction.response.send_message(content=msg)
            else:
                logger.error(f'Erreur App_commands : {error}', exc_info=True)
                if interaction.response.is_done():
                    return await interaction.followup.send(content=f"**Erreur ·** Une erreur est survenue lors de l'exécution de la commande :\n`{error}`")
                return await interaction.response.send_message(content=f"**Erreur ·** Une erreur est survenue lors de l'exécution de la commande :\n`{error}`", delete_after=60)
        
        # Synchronisation des commandes ---------------------------
        
        @bot.command(name='sync')
        @commands.guild_only()
        @commands.is_owner()
        async def sync(ctx: commands.Context, guilds: commands.Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
            """Synchronisation des commandes localement ou globalement
            
            sync -> global sync
            sync ~ -> sync current guild
            sync * -> copies all global app commands to current guild and syncs
            sync ^ -> clears all commands from the current guild target and syncs (removes guild commands)
            sync id_1 id_2 -> syncs guilds with id 1 and 2
            """
            if not guilds:
                if spec == "~":
                    synced = await ctx.bot.tree.sync(guild=ctx.guild)
                elif spec == "*":
                    ctx.bot.tree.copy_global_to(guild=ctx.guild)
                    synced = await ctx.bot.tree.sync(guild=ctx.guild)
                elif spec == "^":
                    ctx.bot.tree.clear_commands(guild=ctx.guild)
                    await ctx.bot.tree.sync(guild=ctx.guild)
                    synced = []
                else:
                    synced = await ctx.bot.tree.sync()

                await ctx.send(
                    f"Synchronisation de {len(synced)} commandes {'globales' if spec is None else 'au serveur actuel'} effectuée." 
                )
                return

            ret = 0
            for guild in guilds:
                try:
                    await ctx.bot.tree.sync(guild=guild)
                except discord.HTTPException:
                    pass
                else:
                    ret += 1

            await ctx.send(f"Arbre synchronisé dans {ret}/{len(guilds)}.")
            
        await bot.start(bot.config['TOKEN']) # type: ignore
            
if __name__ == "__main__":
    asyncio.run(main())
