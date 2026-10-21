"""Build a minimal, pinned official ROS message subset for local contract tests.

Run on Ubuntu with ROS 2 sourced. No sudo, pip, firmware writes or robot access.
This is a test overlay, NOT a vendor firmware compatibility certification.
"""
import argparse
from pathlib import Path
import re
import subprocess
import time
from urllib.request import urlopen

REVISION = '26bd51d9397a46ed3cc008abeb46c8b9f36f3279'
BASE = f'https://raw.githubusercontent.com/VitaDynamics/vbot-lab/{REVISION}/'


def download(path):
    for attempt in range(3):
        try:
            with urlopen(BASE + path, timeout=30) as response:
                return response.read().decode('utf-8')
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    root = Path(args.workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    license_text = download('LICENSE')
    notice = download('NOTICE')
    pending = [('aorta_msgs', 'msg', 'AsrResult'), ('aorta_msgs', 'msg', 'VoiceEvent'),
               ('function_msgs', 'srv', 'SetSpeak')]
    collected = set()
    while pending:
        package, kind, name = pending.pop()
        key = (package, kind, name)
        if key in collected:
            continue
        source = download(f'schemas/ros2/{package}/{kind}/{name}.{kind}')
        target = root / 'src' / package / kind / f'{name}.{kind}'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding='utf-8')
        collected.add(key)
        for line in source.splitlines():
            line = line.split('#', 1)[0].strip()
            if not line or line == '---':
                continue
            field_type = line.split()[0].split('[', 1)[0]
            if re.fullmatch(r'[A-Z][A-Za-z0-9_]*', field_type):
                pending.append((package, 'msg', field_type))
            elif '/' in field_type:
                dep_package, dep_name = field_type.split('/', 1)
                if dep_package not in ('builtin_interfaces', 'std_msgs'):
                    pending.append((dep_package, 'msg', dep_name))
    for package in sorted({p for p, _, _ in collected}):
        directory = root / 'src' / package
        (directory / 'LICENSE').write_text(license_text)
        (directory / 'NOTICE').write_text(notice + '\nTest overlay assembled from ' + REVISION + '\n')
        (directory / 'package.xml').write_text(f'''<?xml version="1.0"?>
<package format="3"><name>{package}</name><version>0.0.0</version>
<description>Official VBOT message subset for Linbot contract tests only</description>
<maintainer email="test@example.invalid">Linbot tests</maintainer><license>Apache-2.0</license>
<buildtool_depend>ament_cmake</buildtool_depend>
<build_depend>rosidl_default_generators</build_depend>
<exec_depend>rosidl_default_runtime</exec_depend>
<depend>builtin_interfaces</depend><depend>std_msgs</depend>
<member_of_group>rosidl_interface_packages</member_of_group>
<export><build_type>ament_cmake</build_type></export></package>
''')
        interfaces = '\n'.join(f'  "{kind}/{name}.{kind}"' for p, kind, name in sorted(collected) if p == package)
        (directory / 'CMakeLists.txt').write_text(f'''cmake_minimum_required(VERSION 3.8)
project({package})
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(std_msgs REQUIRED)
rosidl_generate_interfaces(${{PROJECT_NAME}}
{interfaces}
  DEPENDENCIES builtin_interfaces std_msgs)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
''')
    subprocess.run(['colcon', 'build', '--executor', 'sequential', '--cmake-args',
                    '-DCMAKE_BUILD_TYPE=Release', '-DPYTHON_EXECUTABLE=/usr/bin/python3'], cwd=root, check=True)
    print(f'Test overlay built: {root}/install/setup.bash')


if __name__ == '__main__':
    main()
