import asyncio
import random
from poke_env.player import Player
from poke_env import AccountConfiguration
from poke_env.battle.move_category import MoveCategory
from poke_env.calc.damage_calc_gen9 import calculate_damage

class MyAgent(Player):
    # OLD FIXED CONSTANTS - NO LONGER USED
    # These have been replaced with dynamic calculations based on damage context
    # and turn economy. Kept for reference only.
    # HAZARD_SCORE = 400
    # SETUP_SCORE = 500
    # STATUS_SCORE = 300
    # PROTECT_SCORE = 250
    # DEBUFF_SCORE = 200

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Track one-time utility moves per battle
        self.used_trick = {}  # battle_tag -> bool

    def choose_move(self, battle):
        # Always prioritize moves over switching
        if battle.available_moves:
            # Score all available moves
            move_scores = [
                (move, self.calculate_move_score(battle, move))
                for move in battle.available_moves
            ]

            # Pick the highest scoring move
            best_move, best_score = max(move_scores, key=lambda x: x[1])
            return self.create_order(best_move)

        # Only switch if we have no moves available (e.g., all PP depleted)
        if battle.available_switches:
            switch = max(battle.available_switches, key=lambda p: p.current_hp_fraction)
            return self.create_order(switch)

        # Fallback: random valid order
        return self.choose_random_move(battle)

    # ==================== Helper Methods for Battle State Analysis ====================
    # These methods help evaluate the current battle situation to make strategic decisions

    def count_remaining_mons(self, team):
        """
        Count non-fainted Pokemon in a team.

        Utility: Used to decide whether to set up entry hazards (more valuable when
        opponent has more Pokemon remaining) and whether hazard removal is worthwhile
        (more valuable when we have more Pokemon remaining).
        """
        return sum(1 for mon in team.values() if not mon.fainted)

    def estimate_remaining_turns(self, battle):
        """
        Estimate how many turns remain in the battle.

        Utility: Used to scale status move value by turn economy. Status moves that
        provide value over time (Toxic, Stealth Rock, Setup moves) are more valuable
        in longer battles. This heuristic helps compare immediate damage vs future value.

        Heuristics:
        - Base estimate on current turn number (early/mid/late game)
        - Adjust down if teams are severely weakened (fewer Pokemon = shorter battle)
        - Cap between 3-15 turns to avoid extreme values

        Returns: Estimated remaining turns (int, 3-15)
        """
        turn = battle.turn if hasattr(battle, 'turn') else 0

        # Base estimate from turn number
        if turn <= 5:
            base_estimate = 12  # Early game: ~12 turns remaining
        elif turn <= 15:
            base_estimate = 8   # Mid game: ~8 turns remaining
        else:
            base_estimate = 4   # Late game: ~4 turns remaining

        # Adjust based on remaining Pokemon (fewer mons = shorter battle)
        our_remaining = self.count_remaining_mons(battle.team)
        opp_remaining = self.count_remaining_mons(battle.opponent_team)
        min_remaining = min(our_remaining, opp_remaining)

        # If either team has 1-2 Pokemon left, battle will end sooner
        if min_remaining <= 2:
            base_estimate = min(base_estimate, 5)

        # Clamp to reasonable range
        return max(3, min(15, base_estimate))

    def get_best_damage_score(self, battle, category=None):
        """
        Get the highest damage score among available moves.

        Utility: Used to anchor status move scores to damage output. Instead of using
        arbitrary fixed values (200, 300, etc.), status moves are valued relative to
        the damage we could deal. This creates a unified scoring scale.

        Args:
            battle: The current battle state
            category: Optional MoveCategory filter (PHYSICAL or SPECIAL). If provided,
                     only considers moves of that category. Used for debuffs that only
                     boost specific attack types (Screech for physical, Fake Tears for special).

        Returns: Float representing the highest damage score available, or fallback value
                 of 120 if no damage moves or calculation fails
        """
        best_score = 0

        for move in battle.available_moves:
            # Skip if we're filtering by category and move doesn't match
            if category and move.category != category:
                continue

            # Only consider damaging moves
            if move.category in [MoveCategory.PHYSICAL, MoveCategory.SPECIAL]:
                score = self.evaluate_damage_move(battle, move)
                best_score = max(best_score, score)

        # Fallback: if no damaging moves or all failed, use reasonable default
        # This represents "typical" damage output (100-150 range)
        return best_score if best_score > 0 else 120

    def estimate_matchup(self, mon, opponent):
        """
        Estimate matchup advantage based on type effectiveness, speed, and HP.

        Utility: Determines if we're winning the 1v1 matchup to decide whether it's
        safe to use setup moves. Positive score = favorable matchup (safe to boost),
        negative score = unfavorable matchup (should attack or switch).

        Scoring factors:
        - Offensive type advantage: Can we hit them super effectively?
        - Defensive type advantage: Are we resistant to their attacks?
        - Speed advantage: Do we move first?
        - HP advantage: Are we healthier than them?
        """
        if not mon or not opponent:
            return 0

        score = 0

        # Offensive type advantage
        offensive_advantage = max(
            [opponent.damage_multiplier(t) for t in mon.types if t is not None],
            default=1
        )
        score += (offensive_advantage - 1) * 2  # Scale: -2 to +6

        # Defensive type advantage
        defensive_advantage = max(
            [mon.damage_multiplier(t) for t in opponent.types if t is not None],
            default=1
        )
        score -= (defensive_advantage - 1) * 2  # Penalize weakness

        # Speed advantage
        if mon.base_stats.get("spe", 0) > opponent.base_stats.get("spe", 0):
            score += 1
        elif mon.base_stats.get("spe", 0) < opponent.base_stats.get("spe", 0):
            score -= 1

        # HP advantage
        score += mon.current_hp_fraction * 2
        score -= opponent.current_hp_fraction * 2

        return score

    def can_ohko(self, attacker, defender, move):
        """
        Check if a move can potentially one-hit KO the defender.

        Utility: Used to identify guaranteed knockout opportunities. Could be used
        to prioritize finishing off weakened opponents or to assess risk when
        deciding whether to set up (don't set up if opponent can OHKO us).

        Returns: True if max damage >= defender's HP, False otherwise
        """
        try:
            damage_range = calculate_damage(
                attacker=attacker,
                defender=defender,
                move=move,
                battle=None  # Not all parameters needed for estimate
            )
            if damage_range:
                max_damage = max(damage_range)
                defender_hp = defender.max_hp if defender.max_hp else 100
                return max_damage >= defender_hp
        except:
            pass
        return False

    def is_favorable_setup_situation(self, battle):
        """
        Check if it's a good situation to use setup moves (Swords Dance, etc.).

        Utility: Prevents the bot from setting up in bad situations where it will
        just take heavy damage or get KO'd. Setup moves are only valuable when:
        1. We're at high HP (>80%) - can survive hits while boosting
        2. We have a favorable matchup - won't get destroyed before we can sweep

        Returns: True if safe to set up, False if risky
        """
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if not active or not opponent:
            return False

        # High HP requirement
        if active.current_hp_fraction < 0.8:
            return False

        # Positive matchup
        if self.estimate_matchup(active, opponent) < 0:
            return False

        return True

    # ==================== Move Evaluation Methods ====================
    # These methods score moves to determine which is best to use

    def evaluate_damage_move(self, battle, move):
        """
        Evaluate offensive moves using comprehensive damage calculation.

        Utility: Replaces the old "highest base power" approach with actual damage
        calculation. Now considers ALL damage modifiers including:
        - STAB (Same Type Attack Bonus): 1.5x if move type matches Pokemon type
        - Type effectiveness: 0x to 4x multiplier based on type chart
        - Attack/Defense stats and stat boosts
        - Weather effects (Fire stronger in sun, Water stronger in rain)
        - Items (Choice Band/Specs, Life Orb, etc.)
        - Abilities (Huge Power, Technician, type boosters, etc.)
        - Status conditions (Burn halves physical damage)
        - Screens (Reflect/Light Screen)

        Returns: Average expected damage (used as score to compare with status moves)
        """
        try:
            damage_range = calculate_damage(
                attacker=battle.active_pokemon,
                defender=battle.opponent_active_pokemon,
                move=move,
                battle=battle
            )
            if damage_range:
                # Return average damage as score
                return sum(damage_range) / len(damage_range)
        except:
            # Fallback to base power if calculation fails
            return move.base_power if move.base_power else 0
        return 0
    
    def max_dmg_move(self, battle):
        """
        Identify the move with the highest potential damage.

        """
        best_move = None
        highest_damage = -1

        for move in battle.available_moves:
            if move.category != MoveCategory.STATUS:
                damage = self.evaluate_damage_move(battle, move)
                if damage > highest_damage:
                    highest_damage = damage
                    best_move = move

        return best_move


    def calculate_move_score(self, battle, move):
        """
        Main scoring dispatcher for all moves.

        Utility: Routes each move to the appropriate evaluation function based on
        whether it's an offensive move (Physical/Special) or status move. This
        creates a unified scoring system where all moves (damaging and non-damaging)
        are scored on the same scale, allowing direct comparison.

        Returns: Numeric score (higher = better)
        """
        if move.category == MoveCategory.STATUS:
            return self.evaluate_status_move(battle, move)
        elif move == self.max_dmg_move(battle):
            return self.evaluate_damage_move(battle, move)
        else:
            return 0  # Non-max damage moves get no score
    def evaluate_status_move(self, battle, move):
        """
        Evaluate status moves based on battle context.

        Utility: Determines when status moves are more valuable than attacking.
        Routes to specialized evaluators for different status move categories:
        - Entry hazards (Stealth Rock, Spikes)
        - Setup moves (Swords Dance, Nasty Plot)
        - Status infliction (Toxic, Will-O-Wisp, Thunder Wave)
        - Protection (Protect, Detect)
        - Debuffs (Screech, Intimidate)
        - Utility (Trick, Rapid Spin, U-turn/Volt Switch)

        Returns: Context-based score (0-500+)
        """
        # Entry Hazards (Stealth Rock, Spikes, etc.)
        if move.side_condition:
            return self.evaluate_hazard(battle, move)

        # Setup Moves (Swords Dance, etc.)
        elif move.self_boost:
            return self.evaluate_setup_move(battle, move)

        # Status Infliction (Toxic, etc.)
        elif move.status:
            return self.evaluate_status_infliction(battle, move)

        # Protection (Protect)
        elif move.is_protect_move:
            return self.evaluate_protect(battle, move)

        # Opponent debuffs (Screech, etc.)
        elif move.boosts:
            return self.evaluate_debuff(battle, move)

        # Other utility moves
        else:
            return self.evaluate_utility(battle, move)

    # ==================== Status Move Evaluators ====================
    # Each function evaluates a specific category of status move

    def evaluate_hazard(self, battle, move):
        """
        Evaluate entry hazard moves (Stealth Rock, Spikes, Toxic Spikes).

        Utility: Entry hazards damage opponent Pokemon when they switch in, providing
        passive chip damage throughout the battle. Most valuable when:
        - Opponent has 3+ Pokemon remaining (more switch-ins = more damage)
        - Hazards aren't already set up (no point in redundant setup)

        Strategy: Gliscor should set up Stealth Rock early to wear down opponent team.

        Returns: Expected HP damage from hazards over remaining battle (0-400+)
        """
        opponent_remaining = self.count_remaining_mons(battle.opponent_team)

        # Check if hazard already active
        if move.side_condition in battle.opponent_side_conditions:
            return 0

        # Only worth it if opponent has 3+ Pokemon
        if opponent_remaining >= 3:
            # Estimate 2-3 switches per remaining Pokemon
            expected_switches = opponent_remaining * 2.5

            # Stealth Rock: ~12.5% on neutral, ~25% on 2x weak, ~6% on resistant
            # Use conservative average of ~25 HP per switch
            avg_hazard_damage = 25

            return expected_switches * avg_hazard_damage

        return 0

    def evaluate_setup_move(self, battle, move):
        """
        Evaluate setup moves (Swords Dance, Nasty Plot, Dragon Dance, etc.).

        Utility: Setup moves boost our stats to enable sweeps. Taking a turn to boost
        is only worth it when we can survive and then sweep. Conditions required:
        - Favorable matchup (winning type advantage, high HP > 80%)
        - Can boost further (not already at +6 in that stat)
        - Safe situation (won't get OHKO'd while setting up)

        Strategy: Excadrill should use Swords Dance when healthy and winning matchup,
        then sweep with boosted Earthquake/Iron Head.

        Returns: Extra damage from boosted attacks minus opportunity cost (0-300+)
        """
        active = battle.active_pokemon

        if not self.is_favorable_setup_situation(battle):
            return 0

        # Find best damage move to calculate boost value
        best_damage = self.get_best_damage_score(battle)
        remaining_turns = self.estimate_remaining_turns(battle)

        # Estimate attacks before switch/death (typically 2-4)
        # Conservative: assume 3 attacks, but cap by remaining turns
        expected_attacks = min(3, remaining_turns // 2)

        # Check if we can boost further
        if move.self_boost:
            for stat, boost_amount in move.self_boost.items():
                current_boost = active.boosts.get(stat, 0)

                if boost_amount > 0 and current_boost < 6:
                    # +2 stat boost (standard for Swords Dance, Nasty Plot, etc.)
                    # Each +1 boost = ~1.5x multiplier, so +2 = ~2.25x, but be conservative
                    # Assume ~50% damage increase (1.5x)
                    boosted_damage = best_damage * 1.5
                    extra_damage = (boosted_damage - best_damage) * expected_attacks

                    # Subtract opportunity cost: we spend 1 turn setting up instead of attacking
                    opportunity_cost = best_damage * 0.5

                    return max(0, extra_damage - opportunity_cost)

                elif boost_amount < 0 and current_boost > -6:
                    # Voluntary stat drop (V-create, Close Combat, etc.)
                    # Still worth using but lower value
                    return best_damage * 0.7

        return 0

    def evaluate_status_infliction(self, battle, move):
        """
        Evaluate status-inflicting moves (Toxic, Will-O-Wisp, Thunder Wave).

        Utility: Status conditions cripple opponent Pokemon:
        - Toxic: Increasing damage each turn (great vs bulky Pokemon)
        - Burn: Halves physical attack + residual damage (cripples physical attackers)
        - Paralysis: 25% chance to not move + speed reduction (cripples fast sweepers)
        - Sleep: Can't move for 1-3 turns (effectively removes from battle temporarily)

        Conditions for use:
        - Opponent has no status (Pokemon can only have one major status)
        - Move has decent accuracy (>70%)

        Strategy: Gliscor should use Toxic against bulky Pokemon without status.

        Returns: Expected HP damage from status over remaining turns (0-200+)
        """
        opponent = battle.opponent_active_pokemon

        # Don't use if opponent already has status
        if not opponent or opponent.status:
            return 0

        opponent_hp = opponent.max_hp if opponent.max_hp else 100
        remaining_turns = self.estimate_remaining_turns(battle)

        # Toxic: 1/16, 2/16, 3/16... (increasing damage)
        # Burn: 1/16 per turn + halves physical attack
        # Paralysis: 25% immobilize + speed drop (harder to quantify)
        # Sleep: 1-3 turns of lost actions
        # Average these effects to ~4% HP per turn

        if move.accuracy >= 0.85:
            # Cap at 8 turns to avoid overvaluing
            turns_active = min(remaining_turns, 8)
            hp_damage = opponent_hp * 0.04 * turns_active

            # Factor in accuracy
            return hp_damage * move.accuracy

        # Medium value for less accurate moves
        elif move.accuracy >= 0.7:
            turns_active = min(remaining_turns, 6)
            hp_damage = opponent_hp * 0.03 * turns_active

            return hp_damage * move.accuracy

        return 0

    def evaluate_protect(self, battle, move):
        """
        Evaluate Protect and similar moves (Detect, King's Shield, etc.).

        Utility: Protect blocks the opponent's move for one turn. Uses:
        - Stalling for Poison Heal (Gliscor heals 1/8 HP each turn with Toxic Orb)
        - Scouting opponent's move choice
        - Stalling for weather/terrain damage
        - Burning a turn of opponent's setup

        Diminishing returns: Protect's success rate drops with consecutive uses
        (100% -> 50% -> 25%, tracked by protect_counter)

        Strategy: Gliscor with Poison Heal should use Protect strategically to heal
        while forcing opponent to make moves.

        Returns: HP value of healing or damage avoided (0-150+)
        """
        active = battle.active_pokemon

        # Check protect counter (diminishing returns)
        protect_counter = active.protect_counter if hasattr(active, 'protect_counter') else 0
        if protect_counter >= 2:
            return 0  # Very likely to fail

        # High value for Poison Heal (Gliscor) - direct HP healing
        if active.ability and "poison heal" in active.ability.lower():
            heal_value = (active.max_hp if active.max_hp else 100) * 0.125  # 12.5% HP

            if protect_counter == 0:
                return heal_value  # Full healing value
            else:
                return heal_value * 0.5  # 50% chance to fail

        # Scouting value = avoiding potential damage
        if protect_counter == 0:
            # Estimate opponent's best damage output
            # Use our best damage as proxy (similar power level)
            best_damage = self.get_best_damage_score(battle)
            return best_damage * 0.4  # Avoid ~40% of expected damage

        return 0

    def evaluate_debuff(self, battle, move):
        """
        Evaluate opponent debuff moves (Screech, Charm, Growl, etc.).

        Utility: Debuff moves lower opponent's stats, making them easier to handle:
        - Screech: -2 Defense (makes physical attacks deal massive damage)
        - Charm/Growl: -2 Attack (weakens physical attackers)
        - Fake Tears: -2 Sp. Def (boosts special attack damage)
        - Sticky Web: -1 Speed on switch-in (slows down fast threats)

        Conditions for use:
        - Opponent's stat isn't already debuffed (diminishing returns after first use)
        - For offensive debuffs (Screech/Fake Tears), must have corresponding move type
        - Opponent's stat isn't already debuffed (diminishing returns after first use)
        - For offensive debuffs (Screech/Fake Tears), must have corresponding move type
        - Setting up for a big attack (Screech then physical move)

        Strategy: Swampert can use Screech to soften up bulky Pokemon before
        hitting them with Earthquake.

        Returns: Extra damage from debuff over expected attacks (0-150+)
        """
        opponent = battle.opponent_active_pokemon
        active = battle.active_pokemon

        if not opponent or not move.boosts or not active:
            return 0

        remaining_turns = self.estimate_remaining_turns(battle)
        # Conservative estimate: 2 attacks before opponent switches or faints
        expected_attacks = min(2, remaining_turns // 3)

        # Check if opponent stat isn't already debuffed (diminishing returns)
        for stat, debuff_amount in move.boosts.items():
            if debuff_amount < 0:  # It's a debuff
                current_boost = opponent.boosts.get(stat, 0)

                # Already at cap, useless
                if current_boost <= -6:
                    return 0

                # Context-aware: only use offensive debuffs if we have matching attack type
                if stat == 'def':  # Defense debuff (Screech) - need physical moves
                    best_damage = self.get_best_damage_score(battle, MoveCategory.PHYSICAL)
                    if best_damage == 0:
                        return 0  # No physical moves to follow up, useless

                elif stat == 'spd':  # Sp. Def debuff (Fake Tears) - need special moves
                    best_damage = self.get_best_damage_score(battle, MoveCategory.SPECIAL)
                    if best_damage == 0:
                        return 0  # No special moves to follow up, useless
                else:
                    # Other debuffs (Speed, Accuracy, Evasion)
                    best_damage = self.get_best_damage_score(battle)

                # -2 debuff = ~50% damage increase (Defense/SpDef drops)
                extra_damage_per_hit = best_damage * 0.5
                total_value = extra_damage_per_hit * expected_attacks

                # First use: full value
                if current_boost == 0:
                    return total_value

                # Second use: much lower value (diminishing returns)
                # Only worth it if we don't have better options
                if current_boost >= -2:
                    return total_value * 0.25

                # Third+ use: minimal value
                return total_value * 0.05

        return 0

    def evaluate_utility(self, battle, move):
        """
        Evaluate utility moves (Trick, Rapid Spin, U-turn, Volt Switch, etc.).

        Utility: Catch-all for special-purpose moves that don't fit other categories:

        - Trick: Swap held items (cripple opponent by giving them Choice item)
        - Rapid Spin / Defog: Remove entry hazards from our side
        - U-turn / Volt Switch / Flip Turn: Deal damage AND switch out (momentum)
        - Healing moves: Recover HP
        - Weather setters: Set up sun/rain/sand/hail

        Strategy examples:
        - Chandelure: Use Trick to lock opponent into one move with Choice Specs
        - Excadrill: Use Rapid Spin to clear hazards while dealing damage
        - Swampert: Use Flip Turn to pivot and maintain momentum

        Returns: Varies by move (STATUS_SCORE for Trick, DEBUFF_SCORE for hazard
                 removal, damage score for pivoting moves, 50 as default)
        """
        # Move-specific logic
        move_id = move.id if hasattr(move, 'id') else str(move).lower()

        # Trick (cripple with Choice item) - only effective once per battle
        # Trick (cripple with Choice item) - only effective once per battle
        if "trick" in move_id:
            battle_tag = battle.battle_tag
            if self.used_trick.get(battle_tag, False):
                return 0  # Already used, swapping back is useless

            # Mark as used
            self.used_trick[battle_tag] = True

            # Trick value = crippling opponent for remaining turns
            remaining_turns = self.estimate_remaining_turns(battle)
            best_damage = self.get_best_damage_score(battle)

            # Locking opponent into one move = significant value over remaining turns
            # More valuable in longer battles
            if remaining_turns > 5:
                return best_damage * 1.5  # 150% of a damage move
            else:
                return best_damage * 0.8  # 80% of a damage move in short battles

        # Rapid Spin (hazard removal)
        if "rapidspin" in move_id or "defog" in move_id:
            if battle.side_conditions and self.count_remaining_mons(battle.team) >= 2:
                # Value based on preventing future hazard damage
                our_remaining = self.count_remaining_mons(battle.team)
                # Estimate 1-2 switches per remaining Pokemon
                expected_switches = our_remaining * 1.5
                # Each switch would take ~25 HP damage from hazards
                return expected_switches * 25
            return 0

        # U-turn/Volt Switch (pivoting moves) - use damage calc
        if "uturn" in move_id or "voltswitch" in move_id or "flipturn" in move_id:
            return self.evaluate_damage_move(battle, move)

        # Default low value for unknown utility
        return 50

async def main():
    import logging
    logging.basicConfig(level=logging.INFO)

    # Custom team in Pokemon Showdown's "packed" format
    # Format: Pokemon | Ability | Item | Move1, Move2, Move3, Move4 | Nature | EVs | IVs | Level | Shiny
    custom_team = """
Gliscor @ Toxic Orb  
Ability: Poison Heal  
Tera Type: Water  
EVs: 252 HP / 184 Def / 72 Spe  
Impish Nature  
- Earthquake  
- Toxic  
- Protect  
- Stealth Rock 

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
    username = "MyBot999"
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
