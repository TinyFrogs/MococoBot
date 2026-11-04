-- MococoBot MVP schema (MariaDB 10.5+, utf8mb4)
-- 코드의 SQL 쿼리에서 역추출한 MVP 스키마. 공개본에 스키마 덤프가 없어 새로 작성함.
-- 적용 예시:
--   CREATE DATABASE mococobot CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
--   USE mococobot;  SOURCE schema.sql;  SOURCE seed.sql;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- 직업 (캐릭터가 참조). name -> emoji 매핑. seed.sql에서 채움.
CREATE TABLE IF NOT EXISTS `class` (
  `id`    INT AUTO_INCREMENT PRIMARY KEY,
  `name`  VARCHAR(100) NOT NULL,
  `emoji` VARCHAR(100) NULL,
  UNIQUE KEY `uq_class_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 캐릭터 (로스트아크 API 조회 결과 캐시). 코드에서 `character` 백틱으로 참조됨.
CREATE TABLE IF NOT EXISTS `character` (
  `id`           INT AUTO_INCREMENT PRIMARY KEY,
  `class_id`     INT NULL,
  `char_name`    VARCHAR(100) NOT NULL,
  `item_lvl`     FLOAT NULL,
  `combat_power` FLOAT NULL,
  `updated_at`   DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uq_character_name` (`char_name`),
  KEY `idx_character_class` (`class_id`),
  CONSTRAINT `fk_character_class` FOREIGN KEY (`class_id`) REFERENCES `class` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 레이드 정의 (모집 시 선택). dealer/supporter = 역할별 최대 인원. seed.sql에서 채움.
CREATE TABLE IF NOT EXISTS `raid` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `name`       VARCHAR(100) NOT NULL,
  `difficulty` VARCHAR(50)  NOT NULL,
  `min_lvl`    FLOAT NULL,
  `dealer`     INT NOT NULL DEFAULT 4,
  `supporter`  INT NOT NULL DEFAULT 2,
  `urls`       TEXT NULL,
  KEY `idx_raid_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 길드별 봇 설정.
CREATE TABLE IF NOT EXISTS `server` (
  `id`                     INT AUTO_INCREMENT PRIMARY KEY,
  `guild_id`               VARCHAR(20) NOT NULL,
  `forum_channel_id`       VARCHAR(20) NULL,   -- 레이드 공지(포럼) 채널
  `chat_channel_id`        VARCHAR(20) NULL,   -- 레이드 채팅/시작 알림 채널
  `admin_roles`            TEXT NULL,          -- 관리자 역할 ID (콤마 구분)
  `cancel_join_channel_id` VARCHAR(20) NULL,
  `mention_role_id`        VARCHAR(20) NULL,
  `alert_timer`            TINYINT NOT NULL DEFAULT 0, -- 비트마스크: 1=10분전, 2=1시간전, 3=둘다
  `alert_start`            TINYINT NOT NULL DEFAULT 0, -- 시작 알림 + 참가자 DM 여부
  `waitlist_use`           TINYINT NOT NULL DEFAULT 0, -- 대기자 기능 사용 여부
  UNIQUE KEY `uq_server_guild` (`guild_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 모집된 파티(레이드 일정).
CREATE TABLE IF NOT EXISTS `party` (
  `id`                  INT AUTO_INCREMENT PRIMARY KEY,
  `title`               VARCHAR(255) NOT NULL,
  `raid_id`             INT NULL,
  `guild_id`            VARCHAR(20) NOT NULL,
  `start_date`          DATETIME NULL,
  `owner`               VARCHAR(20) NULL,   -- 공대장 Discord user id
  `message`             VARCHAR(255) NULL,
  `thread_manage_id`    VARCHAR(20) NULL,   -- 포럼 스레드 id
  `is_dealer_closed`    TINYINT NOT NULL DEFAULT 0,
  `is_supporter_closed` TINYINT NOT NULL DEFAULT 0,
  `is_active`           TINYINT NOT NULL DEFAULT 0,
  KEY `idx_party_guild` (`guild_id`),
  KEY `idx_party_start` (`start_date`),
  KEY `idx_party_raid` (`raid_id`),
  CONSTRAINT `fk_party_raid` FOREIGN KEY (`raid_id`) REFERENCES `raid` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 파티 참가자. role: 0=딜러, 1=서포터.
CREATE TABLE IF NOT EXISTS `participants` (
  `id`           INT AUTO_INCREMENT PRIMARY KEY,
  `party_id`     INT NOT NULL,
  `character_id` INT NULL,
  `user_id`      VARCHAR(20) NOT NULL,
  `role`         TINYINT NOT NULL DEFAULT 0,
  `joined_at`    DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_part_party` (`party_id`),
  KEY `idx_part_user` (`user_id`),
  KEY `idx_part_char` (`character_id`),
  CONSTRAINT `fk_part_party` FOREIGN KEY (`party_id`) REFERENCES `party` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_part_char`  FOREIGN KEY (`character_id`) REFERENCES `character` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 대기자. 정원 초과 시 등록, 자리가 나면 participants로 승격(코드에서 삭제+삽입).
CREATE TABLE IF NOT EXISTS `party_waitlist` (
  `id`           INT AUTO_INCREMENT PRIMARY KEY,
  `party_id`     INT NOT NULL,
  `character_id` INT NULL,
  `user_id`      VARCHAR(20) NOT NULL,
  `role`         TINYINT NOT NULL DEFAULT 0,
  `created_at`   DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_wait_party_role` (`party_id`, `role`, `id`),
  CONSTRAINT `fk_wait_party` FOREIGN KEY (`party_id`) REFERENCES `party` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_wait_char`  FOREIGN KEY (`character_id`) REFERENCES `character` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 만료 파티 정리 감사 로그 (scheduler가 기록). party_id 기준 upsert.
CREATE TABLE IF NOT EXISTS `party_audit_event` (
  `id`               INT AUTO_INCREMENT PRIMARY KEY,
  `party_id`         INT NOT NULL,
  `guild_id`         VARCHAR(20) NULL,
  `thread_id`        VARCHAR(20) NULL,
  `party_title`      VARCHAR(255) NULL,
  `raid_name`        VARCHAR(100) NULL,
  `raid_difficulty`  VARCHAR(50) NULL,
  `party_start_date` DATETIME NULL,
  `owner_id`         VARCHAR(20) NULL,
  `status`           VARCHAR(50) NULL,
  `detail`           JSON NULL,
  `attempt_count`    INT NOT NULL DEFAULT 1,
  `created_at`       TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at`       TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY `uq_audit_party` (`party_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 감사 이벤트별 스레드 메시지 스냅샷.
CREATE TABLE IF NOT EXISTS `party_audit_thread_message` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `event_id`   INT NOT NULL,
  `message_id` VARCHAR(20) NOT NULL,
  `author_id`  VARCHAR(20) NULL,
  `created_at` DATETIME NULL,
  `content`    TEXT NULL,
  `raw_json`   JSON NULL,
  UNIQUE KEY `uq_audit_msg` (`event_id`, `message_id`),
  KEY `idx_audit_msg_event` (`event_id`),
  CONSTRAINT `fk_audit_msg_event` FOREIGN KEY (`event_id`) REFERENCES `party_audit_event` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 원정대 (Discord 유저 ↔ 캐릭터 다대다 매핑)
CREATE TABLE IF NOT EXISTS `siblings` (
  `id`           INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`      BIGINT NOT NULL,
  `character_id` INT NOT NULL,
  UNIQUE KEY `uq_siblings` (`user_id`, `character_id`),
  KEY `idx_siblings_user` (`user_id`),
  CONSTRAINT `fk_siblings_char` FOREIGN KEY (`character_id`) REFERENCES `character` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET FOREIGN_KEY_CHECKS = 1;
