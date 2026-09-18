-- Django 테스트 러너가 test_coursemap DB를 만들고 지울 수 있도록 권한 부여.
-- (컨테이너 볼륨이 처음 생성될 때 한 번만 실행된다)
GRANT ALL PRIVILEGES ON `test_coursemap`.* TO 'coursemap'@'%';
FLUSH PRIVILEGES;
