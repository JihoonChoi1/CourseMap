-- Let the Django test runner create and drop the test_coursemap DB.
-- (Runs once, when the container volume is first created.)
GRANT ALL PRIVILEGES ON `test_coursemap`.* TO 'coursemap'@'%';
FLUSH PRIVILEGES;
