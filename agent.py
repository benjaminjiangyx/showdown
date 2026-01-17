import asyncio
import random
from poke_env.player import Player
from poke_env import AccountConfiguration

class MyAgent(Player):
    def choose_move(self, battle):
        # Always prioritize moves over switching
        if battle.available_moves:
            # Sort moves by base_power (descending) and pick the strongest
            sorted_moves = sorted(
                battle.available_moves,
                key=lambda m: getattr(m, "base_power", 0) or 0,
                reverse=True
            )
            # Pick the best available move
            best = sorted_moves[0]
            return self.create_order(best)
        # Only switch if we have no moves available (e.g., all PP depleted)
        if battle.available_switches:
            switch = max(battle.available_switches, key=lambda p: p.current_hp_fraction)
            return self.create_order(switch)
        # Fallback: random valid order
        return self.choose_random_move(battle)

async def main():
    # Custom team in Pokemon Showdown's "packed" format
    # Format: Pokemon | Ability | Item | Move1, Move2, Move3, Move4 | Nature | EVs | IVs | Level | Shiny
    custom_team = """
Tyranitar (F) @ Choice Band  
Ability: Sand Stream  
Tera Type: Ghost  
EVs: 4 HP / 252 Atk / 252 Spe  
Adamant Nature  
- Knock Off  
- Stone Edge  
- Ice Punch  
- Low Kick  

Excadrill (F) @ Air Balloon  
Ability: Sand Rush  
Tera Type: Dragon  
EVs: 4 HP / 252 Atk / 252 Spe  
Adamant Nature  
- Swords Dance  
- Earthquake  
- Iron Head  
- Rapid Spin  

Swampert @ Rocky Helmet  
Ability: Torrent  
Tera Type: Water  
EVs: 252 HP / 4 Def / 252 SpD  
Sassy Nature  
- Earthquake  
- Flip Turn  
- Knock Off  
- Screech  

Goodra-Hisui @ Choice Specs  
Ability: Gooey  
Tera Type: Fairy  
EVs: 252 HP / 252 SpA / 4 SpD  
Quiet Nature  
IVs: 0 Atk  
- Draco Meteor  
- Flash Cannon  
- Fire Blast  
- Thunderbolt  

Gliscor @ Toxic Orb  
Ability: Poison Heal  
Tera Type: Water  
EVs: 252 HP / 184 Def / 72 Spe  
Impish Nature  
- Earthquake  
- Toxic  
- Protect  
- Stealth Rock  

Chandelure @ Choice Specs  
Ability: Flame Body  
Tera Type: Grass  
EVs: 252 SpA / 4 SpD / 252 Spe  
Timid Nature  
IVs: 0 Atk  
- Flamethrower  
- Shadow Ball  
- Energy Ball  
- Trick  
"""

    # Bot will wait for challenges from human players
    # Generate unique username to avoid conflicts
    username = f"MyBot{random.randint(100, 999)}"
    account = AccountConfiguration(username, None)
    agent = MyAgent(
        account_configuration=account,
        battle_format="gen9ou",
        team=custom_team,
        max_concurrent_battles=10
    )

    print(f"Bot is running and accepting battles!")
    print(f"Bot username: {agent.username}")
    print(f"Go to http://localhost:8000")
    print(f"Challenge '{agent.username}' to a gen9ou battle")
    print("Press Ctrl+C to stop the bot\n")

    # Keep bot running indefinitely, accepting challenges
    while True:
        try:
            await agent.accept_challenges(None, n_challenges=1)
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
