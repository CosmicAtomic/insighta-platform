from query_parser import get_cache_key
a = {'gender': 'FEMALE', 'country_id': 'ng', 'min_age': '25'}
b = {'min_age': 25, 'country_id': 'NG', 'gender': 'female'}
print('Both keys match:', get_cache_key(a) == get_cache_key(b))