import asyncio
from poke_env.player import Player
from poke_env.player import RandomPlayer
from poke_env.server_configuration import LocalhostServerConfiguration

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
Tera Type: Ghost  
EVs: 252 SpA / 4 SpD / 252 Spe  
Timid Nature  
IVs: 0 Atk  
- Flamethrower  
- Shadow Ball  
- Energy Ball  
- Trick  
"""

    # For development it's easiest to run against a local server config
    server_config = LocalhostServerConfiguration
    # Change to gen9ou (OverUsed tier) which allows custom teams
    agent = MyAgent(battle_format="gen9ou", team=custom_team, server_configuration=server_config, max_concurrent_battles=1)
    opponent = RandomPlayer(battle_format="gen9ou", server_configuration=server_config)

    # Start one async battle between them
    await agent.battle_against(opponent, n_battles=1)
    print(f"MyAgent won {agent.n_won_battles}/{agent.n_finished_battles}")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
