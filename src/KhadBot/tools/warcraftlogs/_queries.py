"""
GraphQL query definitions for WarcraftLogs v2 API.

Each query is a module-level constant so it can be imported, tested, and
swapped without touching the tool functions that use them.

Performance summary queries (used by fetch_report_summary / get_warcraftlogs_report):
  GET_REPORT_FIGHTS        — fight list + friendly player IDs + actor roster
  GET_PLAYER_DAMAGE_TABLE  — per-player totals and active-time %
  GET_ABILITY_DAMAGE_TABLE — per-ability breakdown (scoped to one player)
  GET_PLAYER_RANKINGS      — percentile + All-Stars points
  GET_PLAYER_CASTS         — raw cast event stream for a specific player

Encounter analysis queries (used by fetch_encounter_events / get_encounter_analysis):
  GET_DEATHS               — death events with killing blow and overkill metadata
  GET_DAMAGE_TAKEN_EVENTS  — raw damage-taken event stream (pre-death window scans)
  GET_AVOIDABLE_DAMAGE     — damage-taken table grouped by ability (avoidable spell ranking)
  GET_HEALING_TABLE        — per-healer effective/overheal breakdown
  GET_COOLDOWN_CASTS       — cast events scoped to fight window (CD timing analysis)
"""

# ---------------------------------------------------------------------------
# Performance summary — 1. Fight list + actor roster
# ---------------------------------------------------------------------------

GET_REPORT_FIGHTS = """
query GetReportFights($code: String!) {
  reportData {
    report(code: $code) {
      title
      startTime
      endTime
      zone {
        name
      }
      masterData {
        actors(type: "Player") {
          id
          name
          type
          subType
        }
      }
      fights(killType: Encounters) {
        id
        name
        difficulty
        kill
        startTime
        endTime
        lastPhase
        friendlyPlayers
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Performance summary — 2. Per-player damage totals
# ---------------------------------------------------------------------------

GET_PLAYER_DAMAGE_TABLE = """
query GetPlayerDamageTable(
  $code: String!
  $fightIDs: [Int]
) {
  reportData {
    report(code: $code) {
      table(
        dataType: DamageDone
        fightIDs: $fightIDs
        viewBy: Source
      )
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Performance summary — 3. Per-ability damage breakdown
# ---------------------------------------------------------------------------

GET_ABILITY_DAMAGE_TABLE = """
query GetAbilityDamageTable(
  $code: String!
  $fightIDs: [Int]
  $sourceID: Int
) {
  reportData {
    report(code: $code) {
      table(
        dataType: DamageDone
        fightIDs: $fightIDs
        sourceID: $sourceID
        viewBy: Ability
      )
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Performance summary — 4. Parse rankings (percentile + All-Stars)
# ---------------------------------------------------------------------------

GET_PLAYER_RANKINGS = """
query GetPlayerRankings(
  $code: String!
  $fightIDs: [Int]
  $playerMetric: ReportRankingMetricType
) {
  reportData {
    report(code: $code) {
      rankings(
        fightIDs: $fightIDs
        playerMetric: $playerMetric
      )
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Performance summary — 5. Raw cast event stream for a single player
# ---------------------------------------------------------------------------

GET_PLAYER_CASTS = """
query GetPlayerCasts(
  $code: String!
  $fightIDs: [Int]
  $sourceID: Int!
  $startTime: Float
  $endTime: Float
) {
  reportData {
    report(code: $code) {
      events(
        dataType: Casts
        fightIDs: $fightIDs
        sourceID: $sourceID
        startTime: $startTime
        endTime: $endTime
        limit: 2000
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

# ===========================================================================
# Encounter analysis queries
# ===========================================================================

# ---------------------------------------------------------------------------
# Encounter — 1. Death events
#
#   Returns one event per player death containing:
#     timestamp  — ms offset from report start
#     targetID   — actor ID of the player who died
#     overkill   — damage dealt beyond 0 HP (magnitude of the kill)
#     ability    — the killing blow (name + id)
#
#   startTime/endTime must be report-absolute (fight.startTime +
#   fight-relative offset).  Caller provides these from the target fight node.
# ---------------------------------------------------------------------------

GET_DEATHS = """
query GetDeaths(
  $code: String!
  $fightIDs: [Int]
  $startTime: Float!
  $endTime: Float!
) {
  reportData {
    report(code: $code) {
      events(
        dataType: Deaths
        fightIDs: $fightIDs
        startTime: $startTime
        endTime: $endTime
        limit: 200
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Encounter — 2. Raw damage-taken event stream
#
#   Fetches incoming damage events within a time window, optionally scoped
#   to a single target player (targetID).  Primary use: pre-death window
#   reconstruction — caller sets startTime = death_ts - PRE_DEATH_WINDOW_MS,
#   endTime = death_ts to get the final seconds of damage before each death.
#
#   Without targetID: returns damage taken by all players — useful for broad
#   avoidable-damage spike detection across the raid.
# ---------------------------------------------------------------------------

GET_DAMAGE_TAKEN_EVENTS = """
query GetDamageTakenEvents(
  $code: String!
  $fightIDs: [Int]
  $startTime: Float!
  $endTime: Float!
  $targetID: Int
) {
  reportData {
    report(code: $code) {
      events(
        dataType: DamageTaken
        fightIDs: $fightIDs
        startTime: $startTime
        endTime: $endTime
        targetID: $targetID
        limit: 2000
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Encounter — 3. Avoidable damage table (per ability, all players)
#
#   Returns damage-taken grouped viewBy: Ability for the full fight.
#   Caller filters the result against a registry of avoidable spell IDs
#   (from Wipefest fight configs / WoWAnalyzer spell dicts) to surface
#   which avoidable mechanics cost the raid the most total HP.
#
#   Returning all abilities rather than filtering server-side keeps the
#   query generic — the avoidable spell registry lives in Python, not GQL.
# ---------------------------------------------------------------------------

GET_AVOIDABLE_DAMAGE = """
query GetAvoidableDamage(
  $code: String!
  $fightIDs: [Int]
  $startTime: Float!
  $endTime: Float!
) {
  reportData {
    report(code: $code) {
      table(
        dataType: DamageTaken
        fightIDs: $fightIDs
        startTime: $startTime
        endTime: $endTime
        viewBy: Ability
      )
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Encounter — 4. Healing breakdown (per healer)
#
#   Effective healing and overheal totals per healer for the fight window.
#   Low overheal % + very high HPS = healers were overwhelmed during this
#   pull — context that reframes death and damage events upstream.
# ---------------------------------------------------------------------------

GET_HEALING_TABLE = """
query GetHealingTable(
  $code: String!
  $fightIDs: [Int]
  $startTime: Float!
  $endTime: Float!
) {
  reportData {
    report(code: $code) {
      table(
        dataType: Healing
        fightIDs: $fightIDs
        startTime: $startTime
        endTime: $endTime
        viewBy: Source
      )
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Encounter — 5. Cooldown cast events (all players, full fight)
#
#   All cast events in the fight window, unfiltered by player.  Caller
#   filters by ability ID against a major-cooldown registry to build a
#   timeline of when each CD was used and by whom.  Not scoped to a single
#   source because cooldown *coordination* analysis — overlaps, gaps,
#   correct windows — requires seeing all players simultaneously.
# ---------------------------------------------------------------------------

GET_COOLDOWN_CASTS = """
query GetCooldownCasts(
  $code: String!
  $fightIDs: [Int]
  $startTime: Float!
  $endTime: Float!
) {
  reportData {
    report(code: $code) {
      events(
        dataType: Casts
        fightIDs: $fightIDs
        startTime: $startTime
        endTime: $endTime
        limit: 2000
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Character lookup — recent reports for a named character
#
#   Returns the N most recent reports in which this character appears,
#   each already containing the full fight list.  This allows the agent to
#   resolve "find my most recent Gnarlroot wipe" in a single API call
#   without knowing a report code upfront.
#
#   serverSlug must be a WarcraftLogs-format slug (lowercase, hyphens).
#   serverRegion is the two-letter region code: "us", "eu", "tw", "kr", "cn".
#
#   recentReports(limit:) accepts 1–5.  We default to 5 so the agent has
#   enough history to disambiguate "last Tuesday's raid" vs "most recent kill".
# ---------------------------------------------------------------------------

GET_CHARACTER_REPORTS = """
query GetCharacterReports(
  $name: String!
  $serverSlug: String!
  $serverRegion: String!
  $reportsLimit: Int
) {
  characterData {
    character(
      name: $name
      serverSlug: $serverSlug
      serverRegion: $serverRegion
    ) {
      name
      classID
      recentReports(limit: $reportsLimit) {
        data {
          code
          title
          startTime
          endTime
          zone {
            name
          }
          fights(killType: Encounters) {
            id
            name
            difficulty
            kill
            startTime
            endTime
            lastPhase
          }
        }
      }
    }
  }
}
"""
