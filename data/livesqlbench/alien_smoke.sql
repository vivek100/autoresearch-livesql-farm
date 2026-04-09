BEGIN;
SET client_min_messages TO WARNING;
DROP SCHEMA IF EXISTS "alien_smoke" CASCADE;
CREATE SCHEMA IF NOT EXISTS "alien_smoke";

DROP TABLE IF EXISTS "alien_smoke"."observatories" CASCADE;
CREATE TABLE "alien_smoke"."observatories" ("observstation" TEXT, "weathprofile" TEXT, "seeingprofile" TEXT, "atmostransparency" DOUBLE PRECISION, "lunarstage" TEXT, "lunardistdeg" DOUBLE PRECISION, "solarstatus" TEXT, "geomagstatus" TEXT, "sidereallocal" TEXT, "airtempc" DOUBLE PRECISION, "humidityrate" DOUBLE PRECISION, "windspeedms" DOUBLE PRECISION, "presshpa" DOUBLE PRECISION);
INSERT INTO "alien_smoke"."observatories" ("observstation", "weathprofile", "seeingprofile", "atmostransparency", "lunarstage", "lunardistdeg", "solarstatus", "geomagstatus", "sidereallocal", "airtempc", "humidityrate", "windspeedms", "presshpa") VALUES
('Observatory-East Darrenport                                 ', 'Clear', 'Good', 0.04, 'First Quarter', 125.94, 'High', 'Quiet', '17.2762 ', 37.6, 21.5, 26.7, 1028.0),
('Observatory-Pearsonstad                                     ', 'Clear', 'Poor', 0.25, 'Full', 100.56, 'Low', 'Quiet', '17.5804 ', -16.9, 67.7, 22.0, 1022.3),
('Observatory-New Lindastad                                   ', 'Cloudy', 'Good', 0.21, 'Last Quarter', 98.09, 'High', 'Storm', '10.9481 ', -12.5, 25.7, 4.4, 1011.0),
('Observatory-Port Vanessastad                                ', 'Partially Cloudy', 'Excellent', 0.28, 'Full', 69.6, 'Moderate', 'Quiet', '19.6423 ', -3.3, 61.7, 23.8, 1008.5),
('Observatory-Oliviachester                                   ', 'Partially Cloudy', 'Excellent', 0.47, 'Last Quarter', 108.11, 'Moderate', 'Quiet', '2.1064  ', 11.8, 34.8, 6.7, 984.6);

COMMIT;
