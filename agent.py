import asyncio
from poke_env.player import Player
from poke_env.player import RandomPlayer
from poke_env.server_configuration import LocalhostServerConfiguration

class MyAgent(Player):
    def choose_move(self, battle):
        # If there are moves available, pick the highest base_power move
        if battle.available_moves:
            best = max(battle.available_moves, key=lambda m: getattr(m, "base_power", 0) or 0)
            return self.create_order(best)
        # Otherwise, pick a switch if possible
        if battle.available_switches:
            switch = max(battle.available_switches, key=lambda p: p.current_hp_fraction)
            return self.create_order(switch)
        # Fallback: random valid order
        return self.choose_random_move(battle)

async def main():
    # For development it's easiest to run against a local server config
    server_config = LocalhostServerConfiguration
    agent = MyAgent(battle_format="gen9randombattle", server_configuration=server_config, max_concurrent_battles=1)
    opponent = RandomPlayer(battle_format="gen9randombattle", server_configuration=server_config)

    # Start one async battle between them
    await agent.battle_against(opponent, n_battles=1)
    print(f"MyAgent won {agent.n_won_battles}/{agent.n_finished_battles}")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
