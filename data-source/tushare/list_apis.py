from api_registry import ALL_APIS

usable = []
unusable = []

for api in ALL_APIS:
    if api.enabled:
        usable.append(f'- `{api.api_name}`: {api.description}')
    else:
        unusable.append(f'- `{api.api_name}`: {api.description}')

print('### 可使用接口 (Usable APIs)')
print('\n'.join(usable))
print('\n### 不可使用接口 (Restricted/Unusable APIs)')
print('\n'.join(unusable))
print(f"\nTotal Usable: {len(usable)}")
print(f"Total Unusable: {len(unusable)}")
