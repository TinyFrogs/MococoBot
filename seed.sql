-- MococoBot MVP 시드 데이터 (TEMPLATE)
-- 주의: 공개본에 원본 시드가 없어 예시로 채운 템플릿이다. 실제 운영 값으로 교체할 것.
--  - class.emoji 는 서버 커스텀 이모지 문자열(<:이름:id>)로 채워야 디스코드에 제대로 표시됨.
--  - raid 의 difficulty/min_lvl/dealer/supporter/목록은 본인 길드 기준 로스트아크 데이터로 교체.
-- 적용: USE mococobot; SOURCE schema.sql; SOURCE seed.sql;

-- 직업: name -> emoji. 서포터 판정은 코드의 bot/core/config.py:SUPPORTER_CLASSES 가 담당한다.
INSERT INTO `class` (`name`, `emoji`) VALUES
  ('버서커',''),('디스트로이어',''),('워로드',''),('홀리나이트',''),('슬레이어',''),('발키리',''),
  ('배틀마스터',''),('인파이터',''),('기공사',''),('창술사',''),('스트라이커',''),('브레이커',''),
  ('데빌헌터',''),('블래스터',''),('호크아이',''),('스카우터',''),('건슬링어',''),
  ('아르카나',''),('서머너',''),('바드',''),('소서리스',''),
  ('블레이드',''),('데모닉',''),('리퍼',''),('소울이터',''),
  ('도화가',''),('기상술사',''),('환수사','')
ON DUPLICATE KEY UPDATE `name` = VALUES(`name`);

-- 레이드: (name, difficulty, min_lvl, dealer 최대, supporter 최대). 아래는 형식 예시일 뿐 — 실제 값으로 교체.
-- 8인 레이드 = dealer 6 / supporter 2, 4인 레이드 = dealer 3 / supporter 1 가 일반적.
INSERT INTO `raid` (`name`, `difficulty`, `min_lvl`, `dealer`, `supporter`) VALUES
  ('발탄',   '노말',  1415, 6, 2),
  ('발탄',   '하드',  1445, 6, 2),
  ('비아키스','노말',  1430, 6, 2),
  ('비아키스','하드',  1460, 6, 2),
  ('쿠크세이튼','노말',1475, 6, 2),
  ('아브렐슈드','노말',1490, 6, 2),
  ('카양겔', '노말',  1540, 3, 1),
  ('카양겔', '하드',  1580, 3, 1);
