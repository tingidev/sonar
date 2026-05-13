-- Lahman Baseball Database schema
-- Column renames applied:
--   2B  -> H2B  (doubles)  in Batting, BattingPost, Teams
--   3B  -> H3B  (triples)  in Batting, BattingPost, Teams
--   HomeGames dot-notation: year.key->yearkey, league.key->leaguekey,
--     team.key->teamkey, park.key->parkkey,
--     span.first->span_first, span.last->span_last
--   Parks dot-notation: park.key->parkkey, park.name->parkname,
--     park.alias->parkalias

-- Core tables

DROP TABLE IF EXISTS People;
CREATE TABLE People (
    playerID       varchar(10) PRIMARY KEY,
    birthYear      int,
    birthMonth     int,
    birthDay       int,
    birthCountry   varchar(50),
    birthState     varchar(30),
    birthCity      varchar(50),
    deathYear      int,
    deathMonth     int,
    deathDay       int,
    deathCountry   varchar(50),
    deathState     varchar(30),
    deathCity      varchar(50),
    nameFirst      varchar(50),
    nameLast       varchar(50),
    nameGiven      varchar(255),
    weight         int,
    height         double precision,
    bats           varchar(1),
    throws         varchar(1),
    debut          varchar(10),
    finalGame      varchar(10),
    retroID        varchar(10),
    bbrefID        varchar(10)
);

DROP TABLE IF EXISTS Batting;
CREATE TABLE Batting (
    playerID  varchar(10),
    yearID    int,
    stint     int,
    teamID    varchar(3),
    lgID      varchar(2),
    G         int,
    AB        int,
    R         int,
    H         int,
    H2B       int,
    H3B       int,
    HR        int,
    RBI       int,
    SB        int,
    CS        int,
    BB        int,
    SO        int,
    IBB       int,
    HBP       int,
    SH        int,
    SF        int,
    GIDP      int
);

DROP TABLE IF EXISTS BattingPost;
CREATE TABLE BattingPost (
    yearID    int,
    round     varchar(10),
    playerID  varchar(10),
    teamID    varchar(3),
    lgID      varchar(2),
    G         int,
    AB        int,
    R         int,
    H         int,
    H2B       int,
    H3B       int,
    HR        int,
    RBI       int,
    SB        int,
    CS        int,
    BB        int,
    SO        int,
    IBB       int,
    HBP       int,
    SH        int,
    SF        int,
    GIDP      int
);

DROP TABLE IF EXISTS Pitching;
CREATE TABLE Pitching (
    playerID  varchar(10),
    yearID    int,
    stint     int,
    teamID    varchar(3),
    lgID      varchar(2),
    W         int,
    L         int,
    G         int,
    GS        int,
    CG        int,
    SHO       int,
    SV        int,
    IPouts    int,
    H         int,
    ER        int,
    HR        int,
    BB        int,
    SO        int,
    BAOpp     double precision,
    ERA       double precision,
    IBB       int,
    WP        int,
    HBP       int,
    BK        int,
    BFP       int,
    GF        int,
    R         int,
    SH        int,
    SF        int,
    GIDP      int
);

DROP TABLE IF EXISTS PitchingPost;
CREATE TABLE PitchingPost (
    playerID  varchar(10),
    yearID    int,
    round     varchar(10),
    teamID    varchar(3),
    lgID      varchar(2),
    W         int,
    L         int,
    G         int,
    GS        int,
    CG        int,
    SHO       int,
    SV        int,
    IPouts    int,
    H         int,
    ER        int,
    HR        int,
    BB        int,
    SO        int,
    BAOpp     double precision,
    ERA       double precision,
    IBB       int,
    WP        int,
    HBP       int,
    BK        int,
    BFP       int,
    GF        int,
    R         int,
    SH        int,
    SF        int,
    GIDP      int
);

DROP TABLE IF EXISTS Fielding;
CREATE TABLE Fielding (
    playerID  varchar(10),
    yearID    int,
    stint     int,
    teamID    varchar(3),
    lgID      varchar(2),
    POS       varchar(2),
    G         int,
    GS        int,
    InnOuts   int,
    PO        int,
    A         int,
    E         int,
    DP        int,
    PB        int,
    WP        int,
    SB        int,
    CS        int,
    ZR        double precision
);

DROP TABLE IF EXISTS FieldingOF;
CREATE TABLE FieldingOF (
    playerID  varchar(10),
    yearID    int,
    stint     int,
    Glf       int,
    Gcf       int,
    Grf       int
);

DROP TABLE IF EXISTS FieldingOFsplit;
CREATE TABLE FieldingOFsplit (
    playerID  varchar(10),
    yearID    int,
    stint     int,
    teamID    varchar(3),
    lgID      varchar(2),
    POS       varchar(2),
    G         int,
    GS        int,
    InnOuts   int,
    PO        int,
    A         int,
    E         int,
    DP        int,
    PB        int,
    WP        int,
    SB        int,
    CS        int,
    ZR        double precision
);

DROP TABLE IF EXISTS FieldingPost;
CREATE TABLE FieldingPost (
    playerID  varchar(10),
    yearID    int,
    teamID    varchar(3),
    lgID      varchar(2),
    round     varchar(10),
    POS       varchar(2),
    G         int,
    GS        int,
    InnOuts   int,
    PO        int,
    A         int,
    E         int,
    DP        int,
    TP        int,
    PB        int,
    SB        int,
    CS        int
);

DROP TABLE IF EXISTS AllstarFull;
CREATE TABLE AllstarFull (
    playerID     varchar(10),
    yearID       int,
    gameNum      int,
    gameID       varchar(12),
    teamID       varchar(3),
    lgID         varchar(2),
    GP           int,
    startingPos  int
);

DROP TABLE IF EXISTS Appearances;
CREATE TABLE Appearances (
    yearID      int,
    teamID      varchar(3),
    lgID        varchar(2),
    playerID    varchar(10),
    G_all       int,
    GS          int,
    G_batting   int,
    G_defense   int,
    G_p         int,
    G_c         int,
    G_1b        int,
    G_2b        int,
    G_3b        int,
    G_ss        int,
    G_lf        int,
    G_cf        int,
    G_rf        int,
    G_of        int,
    G_dh        int,
    G_ph        int,
    G_pr        int
);

DROP TABLE IF EXISTS Managers;
CREATE TABLE Managers (
    playerID  varchar(10),
    yearID    int,
    teamID    varchar(3),
    lgID      varchar(2),
    inseason  int,
    G         int,
    W         int,
    L         int,
    rank      int,
    plyrMgr   varchar(1)
);

DROP TABLE IF EXISTS ManagersHalf;
CREATE TABLE ManagersHalf (
    playerID  varchar(10),
    yearID    int,
    teamID    varchar(3),
    lgID      varchar(2),
    inseason  int,
    half      int,
    G         int,
    W         int,
    L         int,
    rank      int
);

DROP TABLE IF EXISTS Teams;
CREATE TABLE Teams (
    yearID           int,
    lgID             varchar(2),
    teamID           varchar(3),
    franchID         varchar(3),
    divID            varchar(1),
    Rank             int,
    G                int,
    Ghome            int,
    W                int,
    L                int,
    DivWin           varchar(1),
    WCWin            varchar(1),
    LgWin            varchar(1),
    WSWin            varchar(1),
    R                int,
    AB               int,
    H                int,
    H2B              int,
    H3B              int,
    HR               int,
    BB               int,
    SO               int,
    SB               int,
    CS               int,
    HBP              int,
    SF               int,
    RA               int,
    ER               int,
    ERA              double precision,
    CG               int,
    SHO              int,
    SV               int,
    IPouts           int,
    HA               int,
    HRA              int,
    BBA              int,
    SOA              int,
    E                int,
    DP               int,
    FP               double precision,
    name             varchar(50),
    park             varchar(255),
    attendance       int,
    BPF              int,
    PPF              int,
    teamIDBR         varchar(3),
    teamIDlahman45   varchar(3),
    teamIDretro      varchar(3)
);

DROP TABLE IF EXISTS TeamsFranchises;
CREATE TABLE TeamsFranchises (
    franchID    varchar(3),
    franchName  varchar(50),
    active      varchar(2),
    NAassoc     varchar(3)
);

DROP TABLE IF EXISTS TeamsHalf;
CREATE TABLE TeamsHalf (
    yearID  int,
    lgID    varchar(2),
    teamID  varchar(3),
    Half    varchar(1),
    divID   varchar(1),
    DivWin  varchar(1),
    Rank    int,
    G       int,
    W       int,
    L       int
);

DROP TABLE IF EXISTS SeriesPost;
CREATE TABLE SeriesPost (
    yearID        int,
    round         varchar(5),
    teamIDwinner  varchar(3),
    lgIDwinner    varchar(2),
    teamIDloser   varchar(3),
    lgIDloser     varchar(2),
    wins          int,
    losses        int,
    ties          int
);

DROP TABLE IF EXISTS HomeGames;
CREATE TABLE HomeGames (
    yearkey     int,
    leaguekey   varchar(2),
    teamkey     varchar(3),
    parkkey     varchar(10),
    span_first  date,
    span_last   date,
    games       int,
    openings    int,
    attendance  int
);

DROP TABLE IF EXISTS Parks;
CREATE TABLE Parks (
    parkkey    varchar(10),
    parkname   varchar(100),
    parkalias  varchar(100),
    city       varchar(50),
    state      varchar(30),
    country    varchar(50)
);

-- Contrib tables

DROP TABLE IF EXISTS AwardsManagers;
CREATE TABLE AwardsManagers (
    playerID  varchar(10),
    awardID   varchar(75),
    yearID    int,
    lgID      varchar(2),
    tie       varchar(1),
    notes     varchar(100)
);

DROP TABLE IF EXISTS AwardsPlayers;
CREATE TABLE AwardsPlayers (
    playerID  varchar(10),
    awardID   varchar(75),
    yearID    int,
    lgID      varchar(2),
    tie       varchar(1),
    notes     varchar(100)
);

DROP TABLE IF EXISTS AwardsShareManagers;
CREATE TABLE AwardsShareManagers (
    awardID     varchar(25),
    yearID      int,
    lgID        varchar(2),
    playerID    varchar(10),
    pointsWon   double precision,
    pointsMax   int,
    votesFirst  double precision
);

DROP TABLE IF EXISTS AwardsSharePlayers;
CREATE TABLE AwardsSharePlayers (
    awardID     varchar(25),
    yearID      int,
    lgID        varchar(2),
    playerID    varchar(10),
    pointsWon   double precision,
    pointsMax   int,
    votesFirst  double precision
);

DROP TABLE IF EXISTS CollegePlaying;
CREATE TABLE CollegePlaying (
    playerID  varchar(10),
    schoolID  varchar(15),
    yearID    int
);

DROP TABLE IF EXISTS HallOfFame;
CREATE TABLE HallOfFame (
    playerID     varchar(10),
    yearID       int,
    votedBy      varchar(64),
    ballots      int,
    needed       int,
    votes        int,
    inducted     varchar(1),
    category     varchar(20),
    needed_note  varchar(25)
);

DROP TABLE IF EXISTS Salaries;
CREATE TABLE Salaries (
    yearID    int,
    teamID    varchar(3),
    lgID      varchar(2),
    playerID  varchar(10),
    salary    double precision
);

DROP TABLE IF EXISTS Schools;
CREATE TABLE Schools (
    schoolID   varchar(15),
    name_full  varchar(255),
    city       varchar(55),
    state      varchar(55),
    country    varchar(55)
);
