web: cd backend && python manage.py migrate && python manage.py collectstatic --noinput && python manage.py import_constituency_geojson ../data/tn_ac_2021.geojson && gunicorn tntracker.wsgi --bind 0.0.0.0:$PORT
release: cd backend && python manage.py migrate
