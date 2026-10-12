"""
encounter/ingest/wcl_queries.py

GraphQL for encounter ingest: one report-metadata query and one paged events query.

Field names marked UNCERTAIN have not been confirmed against the live schema; the
cost-measurement run (scripts/spike_wcl_cost.py) verifies them by introspection.
"""

GET_REPORT_META = """
query GetReportMeta($code: String!) {
  reportData {
    report(code: $code) {
      title
      startTime
      zone {
        id
        name
      }
      masterData {
        actors {
          id
          name
          type
          subType
          petOwner
          gameID
        }
        abilities {
          gameID
          name
          type
        }
      }
      fights(killType: Encounters) {
        id
        encounterID
        name
        difficulty
        kill
        startTime
        endTime
        lastPhase
        fightPercentage
        bossPercentage
        size
        friendlyPlayers
        phaseTransitions {
          id
          startTime
        }
      }
    }
  }
}
"""

# UNCERTAIN: includeResources (hitPoints/maxHitPoints semantics per event type) and the
# EventDataType / HostilityType enum spellings.
GET_EVENTS_PAGE = """
query GetEventsPage(
  $code: String!
  $fightIDs: [Int]!
  $startTime: Float!
  $endTime: Float!
  $dataType: EventDataType!
  $hostility: HostilityType
  $filter: String
  $limit: Int
) {
  reportData {
    report(code: $code) {
      events(
        fightIDs: $fightIDs
        startTime: $startTime
        endTime: $endTime
        dataType: $dataType
        hostilityType: $hostility
        filterExpression: $filter
        includeResources: true
        limit: $limit
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""
