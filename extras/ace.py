import serial, threading, time, logging, json, struct, queue, traceback, re
from serial import SerialException
import serial.tools.list_ports

class BunnyAce:
    def __init__(self, config):
        self._connected = False
        self._serial = None
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        self.lock = False
        self.send_time = None
        self.read_buffer = bytearray()
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        self.variables = self.printer.lookup_object('save_variables').allVariables

        self.serial_name = config.get('serial', '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)
        extruder_sensor_pin = config.get('extruder_sensor_pin', None)
        toolhead_sensor_pin = config.get('toolhead_sensor_pin', None)
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 150)
        self.toolchange_load_length = config.getint('toolchange_load_length', 630)
        self.toolhead_sensor_to_nozzle_length = config.getint('toolhead_sensor_to_nozzle', 0)
        # self.extruder_to_blade_length = config.getint('extruder_to_blade', None)
        self.bowden_tube_length = config.getint('bowden_tube_length', 1000)

        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)

        # 自动续料配置 - 如果可用则从持久变量加载
        saved_endless_spool_enabled = self.variables.get('ace_endless_spool_enabled', False)
        
        self.endless_spool_enabled = config.getboolean('endless_spool', saved_endless_spool_enabled)
        self.endless_spool_in_progress = False
        self.endless_spool_runout_detected = False

        self._callback_map = {}
        self.park_hit_count = 5
        self._feed_assist_index = -1
        self._request_id = 0
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1
        self.endstops = {}

        # 默认数据以防止异常
        self._info = {
            'status': 'ready',
            'dryer': {
                'status': 'stop',
                'target_temp': 0,
                'duration': 0,
                'remain_time': 0
            },
            'temp': 0,
            'enable_rfid': 1,
            'fan_speed': 7000,
            'feed_assist_count': 0,
            'cont_assist_time': 0.0,
            'slots': [
                {
                    'index': 0,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 1,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 2,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 3,
                    'status': 'empty',
                    'sku': '',
                    'type': '',
                    'color': [0, 0, 0]
                }
            ]
        }

        # 为4个料盘添加库存 - 如果可用则从持久变量加载
        saved_inventory = self.variables.get('ace_inventory', None)
        if saved_inventory:
            self.inventory = saved_inventory
        else:
            self.inventory = [
                {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0} for _ in range(4)
            ]
        # 注册库存命令
        self.gcode.register_command(
            'ACE_SET_SLOT', self.cmd_ACE_SET_SLOT,
            desc="设置料盘库存: INDEX= COLOR= MATERIAL= TEMP= | 使用 EMPTY=1 将状态设置为空"
        )
        self.gcode.register_command(
            'ACE_QUERY_SLOTS', self.cmd_ACE_QUERY_SLOTS,
            desc="以 JSON 格式查询所有料盘库存"
        )

        self._create_mmu_sensor(config, extruder_sensor_pin, "extruder_sensor")
        self._create_mmu_sensor(config, toolhead_sensor_pin, "toolhead_sensor")
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)
        self.gcode.register_command(
            'ACE_DEBUG', self.cmd_ACE_DEBUG,
            desc='ACE 调试命令')
        self.gcode.register_command(
            'ACE_START_DRYING', self.cmd_ACE_START_DRYING,
            desc=self.cmd_ACE_START_DRYING_help)
        self.gcode.register_command(
            'ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING,
            desc=self.cmd_ACE_STOP_DRYING_help)
        self.gcode.register_command(
            'ACE_ENABLE_FEED_ASSIST', self.cmd_ACE_ENABLE_FEED_ASSIST,
            desc=self.cmd_ACE_ENABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_DISABLE_FEED_ASSIST', self.cmd_ACE_DISABLE_FEED_ASSIST,
            desc=self.cmd_ACE_DISABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_FEED', self.cmd_ACE_FEED,
            desc=self.cmd_ACE_FEED_help)
        self.gcode.register_command(
            'ACE_RETRACT', self.cmd_ACE_RETRACT,
            desc=self.cmd_ACE_RETRACT_help)
        self.gcode.register_command(
            'ACE_CHANGE_TOOL', self.cmd_ACE_CHANGE_TOOL,
            desc=self.cmd_ACE_CHANGE_TOOL_help)
        self.gcode.register_command(
            'ACE_ENABLE_ENDLESS_SPOOL', self.cmd_ACE_ENABLE_ENDLESS_SPOOL,
            desc=self.cmd_ACE_ENABLE_ENDLESS_SPOOL_help)
        self.gcode.register_command(
            'ACE_DISABLE_ENDLESS_SPOOL', self.cmd_ACE_DISABLE_ENDLESS_SPOOL,
            desc=self.cmd_ACE_DISABLE_ENDLESS_SPOOL_help)
        self.gcode.register_command(
            'ACE_ENDLESS_SPOOL_STATUS', self.cmd_ACE_ENDLESS_SPOOL_STATUS,
            desc=self.cmd_ACE_ENDLESS_SPOOL_STATUS_help)
        self.gcode.register_command(
            'ACE_SAVE_INVENTORY', self.cmd_ACE_SAVE_INVENTORY,
            desc=self.cmd_ACE_SAVE_INVENTORY_help)
        self.gcode.register_command(
            'ACE_TEST_RUNOUT_SENSOR', self.cmd_ACE_TEST_RUNOUT_SENSOR,
            desc=self.cmd_ACE_TEST_RUNOUT_SENSOR_help)
        self.gcode.register_command(
            'ACE_CHANGE_SPOOL', self.cmd_ACE_CHANGE_SPOOL,
            desc=self.cmd_ACE_CHANGE_SPOOL_help)
        self.gcode.register_command(
            'ACE_GET_CURRENT_INDEX', self.cmd_ACE_GET_CURRENT_INDEX,
            desc=self.cmd_ACE_GET_CURRENT_INDEX_help)


    def _calc_crc(self, buffer):
        _crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= _crc & 0xff
            data ^= (data & 0x0f) << 4
            _crc = ((data << 8) | (_crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return _crc

    def _send_request(self, request):
        if not 'id' in request:
            request['id'] = self._request_id
            self._request_id += 1

        payload = json.dumps(request)
        payload = bytes(payload, 'utf-8')

        data = bytes([0xFF, 0xAA])
        data += struct.pack('@H', len(payload))
        data += payload
        data += struct.pack('@H', self._calc_crc(payload))
        data += bytes([0xFE])
        self._serial.write(data)


    def _reader(self, eventtime):

        if self.lock and (self.reactor.monotonic() - self.send_time) > 2:
            self.lock = False
            self.read_buffer = bytearray()
            self.gcode.respond_info(f"超时 {self.reactor.monotonic()}")

        buffer = bytearray()
        try:
            raw_bytes = self._serial.read(size=4096)
        except SerialException:
            self.gcode.respond_info("无法与 ACE PRO 通信" + traceback.format_exc())
            self.lock = False
            self.gcode.respond_info('尝试重新连接')
            self._serial_disconnect()
            self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)
            return self.reactor.NEVER

        if len(raw_bytes):
            text_buffer = self.read_buffer + raw_bytes
            i = text_buffer.find(b'\xfe')
            if i >= 0:
                buffer = text_buffer
                self.read_buffer = bytearray()
            else:
                self.read_buffer += raw_bytes
                return eventtime + 0.1

        else:
            return eventtime + 0.1

        if len(buffer) < 7:
            return eventtime + 0.1

        if buffer[0:2] != bytes([0xFF, 0xAA]):
            self.lock = False
            self.gcode.respond_info("来自 ACE PRO 的无效数据（头部字节）")
            self.gcode.respond_info(str(buffer))
            return eventtime + 0.1

        payload_len = struct.unpack('<H', buffer[2:4])[0]
        logging.info(str(buffer))
        payload = buffer[4:4 + payload_len]

        crc_data = buffer[4 + payload_len:4 + payload_len + 2]
        crc = struct.pack('@H', self._calc_crc(payload))

        if len(buffer) < (4 + payload_len + 2 + 1):
            self.lock = False
            self.gcode.respond_info(f"来自 ACE PRO 的无效数据（长度） {payload_len} {len(buffer)} {crc}")
            self.gcode.respond_info(str(buffer))
            return eventtime + 0.1

        if crc_data != crc:
            self.lock = False
            self.gcode.respond_info('来自 ACE PRO 的无效数据（CRC）')

        ret = json.loads(payload.decode('utf-8'))
        id = ret['id']
        if id in self._callback_map:
            callback = self._callback_map.pop(id)
            callback(self=self, response=ret)
            self.lock = False
        return eventtime + 0.1

    def _writer(self, eventtime):

        try:
            def callback(self, response):
                if response is not None:
                    self._info = response['result']
            if not self.lock:
                if not self._queue.empty():
                    task = self._queue.get()
                    if task is not None:
                        id = self._request_id
                        self._request_id += 1
                        self._callback_map[id] = task[1]
                        task[0]['id'] = id

                        self._send_request(task[0])
                        self.send_time = eventtime
                        self.lock = True
                else:
                    id = self._request_id
                    self._request_id += 1
                    self._callback_map[id] = callback
                    self._send_request({"id": id, "method": "get_status"})
                    self.send_time = eventtime
                    self.lock = True
        except serial.serialutil.SerialException as e:
            logging.info('ACE 错误: ' + traceback.format_exc())
            self.lock = False
            self.gcode.respond_info('尝试重新连接')
            self._serial_disconnect()
            self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)
            return self.reactor.NEVER
        except Exception as e:
            self.gcode.respond_info(str(e))
            logging.info('ACE: 写入错误 ' + str(e))
        return eventtime + 0.5

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        logging.info('ACE: 连接到 ' + self.serial_name)
        # 我们可以捕获主机没有数据可用时 ACE 重新启动的时间。我们通过这个技巧避免它
        self._connected = False
        self._queue = queue.Queue()
        self._main_queue = queue.Queue()
        self.connect_timer = self.reactor.register_timer(self._connect, self.reactor.NOW)
        # 启动自动续料监控定时器
        if hasattr(self, 'endless_spool_enabled'):
            self.endless_spool_timer = self.reactor.register_timer(self._endless_spool_monitor, self.reactor.NOW)
            # 挂接到 gcode 移动事件以进行更广泛的挤出机监控
            self.printer.register_event_handler('toolhead:move', self._on_toolhead_move)


    def _handle_disconnect(self):
        logging.info('ACE: 关闭与 ' + self.serial_name + ' 的连接')
        self._serial.close()
        self._connected = False
        self.reactor.unregister_timer(self.writer_timer)
        self.reactor.unregister_timer(self.reader_timer)
        # 停止自动续料监控
        if hasattr(self, 'endless_spool_timer'):
            self.reactor.unregister_timer(self.endless_spool_timer)

        self._queue = None
        self._main_queue = None

    def dwell(self, delay = 1.):
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)

    def send_request(self, request, callback):
        self._info['status'] = 'busy'
        self._queue.put([request, callback])

    def wait_ace_ready(self):
        while self._info['status'] != 'ready':
            currTs = self.reactor.monotonic()
            self.reactor.pause(currTs + .5)

    def _extruder_move(self, length, speed):
        pos = self.toolhead.get_position()
        pos[3] += length
        self.toolhead.move(pos, speed)
        
        return pos[3]

    def _endless_spool_monitor(self, eventtime):
        """在打印期间监控断料检测"""
        if not self.endless_spool_enabled or self._park_in_progress or self.endless_spool_in_progress:
            return eventtime + 0.1

        # 仅在有活动工具且我们尚未处于断料状态时监控
        current_tool = self.variables.get('ace_current_index', -1)
        if current_tool == -1:
            return eventtime + 0.1

        # 检查我们当前是否正在打印 - 更积极地检测打印状态
        try:
            # 检查多个可能正在打印的指标
            toolhead = self.printer.lookup_object('toolhead')
            print_stats = self.printer.lookup_object('print_stats', None)
            
            is_printing = False
            
            # 方法1：检查工具头是否在移动
            if hasattr(toolhead, 'get_status'):
                toolhead_status = toolhead.get_status(eventtime)
                if 'homed_axes' in toolhead_status and toolhead_status['homed_axes']:
                    is_printing = True
            
            # 方法2：如果可用，检查打印统计信息
            if print_stats:
                stats = print_stats.get_status(eventtime)
                if stats.get('state') in ['printing']:
                    is_printing = True
            
            # 方法3：检查空闲超时状态
            try:
                printer_idle = self.printer.lookup_object('idle_timeout')
                idle_state = printer_idle.get_status(eventtime)['state']
                if idle_state in ['Printing', 'Ready']:  # Ready 意味着可能正在打印
                    is_printing = True
            except:
                # 如果 idle_timeout 不存在，假设我们可能正在打印
                is_printing = True

            # 如果启用了自动续料且有活动工具，始终检查断料
            # 不依赖仅打印状态检测
            if current_tool >= 0:
                self._endless_spool_runout_handler()
            
            # 根据状态调整监控频率
            if is_printing:
                return eventtime + 0.05  # 打印期间每50ms检查一次
            else:
                return eventtime + 0.2   # 空闲时每200ms检查一次
                
        except Exception as e:
            logging.info(f'ACE: 自动续料监控错误: {str(e)}')
            return eventtime + 0.1

    def _on_toolhead_move(self, print_time, newpos, oldpos):
        """监控工具头移动以检测打印期间的挤出机移动 - 移除了距离跟踪"""
        # 此方法保留供将来潜在使用，但移除了距离跟踪
        pass

    def _create_mmu_sensor(self, config, pin, name):
        section = "filament_switch_sensor %s" % name
        config.fileconfig.add_section(section)
        config.fileconfig.set(section, "switch_pin", pin)
        config.fileconfig.set(section, "pause_on_runout", "False")
        fs = self.printer.load_object(config, section)

        ppins = self.printer.lookup_object('pins')
        pin_params = ppins.parse_pin(pin, True, True)
        share_name = "%s:%s" % (pin_params['chip_name'], pin_params['pin'])
        ppins.allow_multi_use_pin(share_name)
        mcu_endstop = ppins.setup_pin('endstop', pin)

        query_endstops = self.printer.load_object(config, "query_endstops")
        query_endstops.register_endstop(mcu_endstop, share_name)
        self.endstops[name] = mcu_endstop

    def _check_endstop_state(self, name):
        print_time = self.toolhead.get_last_move_time()
        return bool(self.endstops[name].query_endstop(print_time))

    def _serial_disconnect(self):

        if self._serial is not None and self._serial.isOpen():
            self._serial.close()
            self._connected = False

        self.reactor.unregister_timer(self.reader_timer)
        self.reactor.unregister_timer(self.writer_timer)

    def _connect(self, eventtime):

        try:
            port = self.find_com_port('ACE')
            if port is None:
                return eventtime + 1
            self.gcode.respond_info('尝试连接')
            self._serial = serial.Serial(
                port=port,
                baudrate=self.baud,
                timeout=0,
                write_timeout=0)

            if self._serial.isOpen():
                self._connected = True
                logging.info('ACE: 已连接到 ' + port)
                self.gcode.respond_info(f'ACE: 已连接到 {port} {eventtime}')
                self.writer_timer = self.reactor.register_timer(self._writer, self.reactor.NOW)
                self.reader_timer = self.reactor.register_timer(self._reader, self.reactor.NOW)
                self.send_request(request={"method": "get_info"},
                                  callback=lambda self, response: self.gcode.respond_info(str(response)))
                # --- 添加：检查 ace_current_index 并在需要时启用进料辅助 ---
                ace_current_index = self.variables.get('ace_current_index', -1)
                if ace_current_index != -1:
                    self.gcode.respond_info(f'ACE: 重新连接时重新启用索引 {ace_current_index} 的进料辅助')
                    self._enable_feed_assist(ace_current_index)
                # ---------------------------------------------------------------
                self.reactor.unregister_timer(self.connect_timer)
                return self.reactor.NEVER
        except serial.serialutil.SerialException:
            self._serial = None
        return eventtime + 1


    cmd_ACE_START_DRYING_help = '启动 ACE Pro 干燥器'

    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMP')
        duration = gcmd.get_int('DURATION', 240)

        if duration <= 0:
            raise gcmd.error('错误的持续时间')
        if temperature <= 0 or temperature > self.max_dryer_temperature:
            raise gcmd.error('错误的温度')

        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE 错误: " + response['msg'])

            self.gcode.respond_info('已启动 ACE 干燥')

        self.send_request(
            request={"method": "drying", "params": {"temp": temperature, "fan_speed": 7000, "duration": duration}},
            callback=callback)

    cmd_ACE_STOP_DRYING_help = '停止 ACE Pro 干燥器'

    def cmd_ACE_STOP_DRYING(self, gcmd):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise gcmd.error("ACE 错误: " + response['msg'])

            self.gcode.respond_info('已停止 ACE 干燥')

        self.send_request(request={"method": "drying_stop"}, callback=callback)

    def _enable_feed_assist(self, index):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE 错误: " + response['msg'])
            else:
                self._feed_assist_index = index
                self.gcode.respond_info(str(response))

        self.send_request(request={"method": "start_feed_assist", "params": {"index": index}}, callback=callback)
        self.dwell(delay=0.7)

    cmd_ACE_ENABLE_FEED_ASSIST_help = '启用 ACE 进料辅助'

    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('错误的索引')

        self._enable_feed_assist(index)

    def _disable_feed_assist(self, index):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE 错误: " + response['msg'])

            self._feed_assist_index = -1
            self.gcode.respond_info('已禁用 ACE 进料辅助')

        self.send_request(request={"method": "stop_feed_assist", "params": {"index": index}}, callback=callback)
        self.dwell(0.3)

    cmd_ACE_DISABLE_FEED_ASSIST_help = '禁用 ACE 进料辅助'

    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        if self._feed_assist_index != -1:
            index = gcmd.get_int('INDEX', self._feed_assist_index)
        else:
            index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('错误的索引')

        self._disable_feed_assist(index)

    def _feed(self, index, length, speed):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE 错误: " + response['msg'])

        self.send_request(
            request={"method": "feed_filament", "params": {"index": index, "length": length, "speed": speed}},
            callback=callback)
        self.dwell(delay=(length / speed) + 0.1)

    cmd_ACE_FEED_help = '从 ACE 进料'

    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.feed_speed)

        if index < 0 or index >= 4:
            raise gcmd.error('错误的索引')
        if length <= 0:
            raise gcmd.error('错误的长度')
        if speed <= 0:
            raise gcmd.error('错误的速度')

        self._feed(index, length, speed)

    def _retract(self, index, length, speed):
        def callback(self, response):
            if 'code' in response and response['code'] != 0:
                raise ValueError("ACE 错误: " + response['msg'])

        self.send_request(
            request={"method": "unwind_filament", "params": {"index": index, "length": length, "speed": speed}},
            callback=callback)
        self.dwell(delay=(length / speed) + 0.1)

    cmd_ACE_RETRACT_help = '将线材回退到 ACE'

    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int('SPEED', self.retract_speed)

        if index < 0 or index >= 4:
            raise gcmd.error('错误的索引')
        if length <= 0:
            raise gcmd.error('错误的长度')
        if speed <= 0:
            raise gcmd.error('错误的速度')

        self._retract(index, length, speed)

    def _park_to_toolhead(self, tool):

        sensor_extruder = self.printer.lookup_object("filament_switch_sensor %s" % "extruder_sensor", None)

        self.wait_ace_ready()

        self._feed(tool, self.toolchange_load_length, self.retract_speed)
        self.variables['ace_filament_pos'] = "bowden"

        self.wait_ace_ready()

        self._enable_feed_assist(tool)

        while not bool(sensor_extruder.runout_helper.filament_present):
            self.dwell(delay=0.1)

        if not bool(sensor_extruder.runout_helper.filament_present):
            raise ValueError("线材卡住 " + str(bool(sensor_extruder.runout_helper.filament_present)))
        else:
            self.variables['ace_filament_pos'] = "spliter"

        while not self._check_endstop_state('toolhead_sensor'):
            self._extruder_move(1, 5)
            self.dwell(delay=0.01)

        self.variables['ace_filament_pos'] = "toolhead"

        self._extruder_move(self.toolhead_sensor_to_nozzle_length, 5)
        self.variables['ace_filament_pos'] = "nozzle"

    cmd_ACE_CHANGE_TOOL_help = '更换工具'

    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        tool = gcmd.get_int('TOOL')
        sensor_extruder = self.printer.lookup_object("filament_switch_sensor %s" % "extruder_sensor", None)

        if tool < -1 or tool >= 4:
            raise gcmd.error('错误的工具')

        was = self.variables.get('ace_current_index', -1)
        if was == tool:
            gcmd.respond_info('ACE: 未更换工具，当前索引已是 ' + str(tool))
            self._enable_feed_assist(tool)
            return

        if tool != -1:
            status = self._info['slots'][tool]['status']
            if status != 'ready':
                self.gcode.run_script_from_command('_ACE_ON_EMPTY_ERROR INDEX=' + str(tool))
                return
        
        # 在手动工具更换期间暂时禁用自动续料
        endless_spool_was_enabled = self.endless_spool_enabled
        if endless_spool_was_enabled:
            self.endless_spool_enabled = False
            self.endless_spool_runout_detected = False
        self._park_in_progress = True
        self.gcode.run_script_from_command('_ACE_PRE_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))

        logging.info('ACE: 工具更换 ' + str(was) + ' => ' + str(tool))
        if was != -1:
            self._disable_feed_assist(was)
            self.wait_ace_ready()
            if self.variables.get('ace_filament_pos', "spliter") == "nozzle":
                self.gcode.run_script_from_command('CUT_TIP')
                self.variables['ace_filament_pos'] = "toolhead"

            if self.variables.get('ace_filament_pos', "spliter") == "toolhead":
                while bool(sensor_extruder.runout_helper.filament_present):
                    self._extruder_move(-50, 10)
                    self._retract(was, 100, self.retract_speed)
                    self.wait_ace_ready()
                self.variables['ace_filament_pos'] = "bowden"

            self.wait_ace_ready()

            self._retract(was, self.toolchange_retract_length, self.retract_speed)
            self.wait_ace_ready()
            self.variables['ace_filament_pos'] = "spliter"

            if tool != -1:
                self._park_to_toolhead(tool)
        else:
            self._park_to_toolhead(tool)

        gcode_move = self.printer.lookup_object('gcode_move')
        gcode_move.reset_last_position()

        self.gcode.run_script_from_command('_ACE_POST_TOOLCHANGE FROM=' + str(was) + ' TO=' + str(tool))
        self.variables['ace_current_index'] = tool
        gcode_move.reset_last_position()
        # 强制保存到磁盘
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(tool))
        self.gcode.run_script_from_command(
            f"""SAVE_VARIABLE VARIABLE=ace_filament_pos VALUE='"{self.variables['ace_filament_pos']}"'""")
        self._park_in_progress = False
        
        # 如果之前启用了自动续料，则重新启用
        if endless_spool_was_enabled:
            self.endless_spool_enabled = True
            
        gcmd.respond_info(f"工具 {tool} 已加载")

    def _find_next_available_slot(self, current_slot):
        """为自动续料查找下一个有线的可用料盘"""
        for i in range(4):
            next_slot = (current_slot + 1 + i) % 4
            if next_slot != current_slot:
                # 检查库存和 ACE 状态
                if (self.inventory[next_slot]["status"] == "ready" and 
                    self._info['slots'][next_slot]['status'] == 'ready'):
                    return next_slot
        return -1  # 没有可用料盘

    def _endless_spool_runout_handler(self):
        """处理自动续料的断料检测"""
        if not self.endless_spool_enabled or self.endless_spool_in_progress:
            return

        current_tool = self.variables.get('ace_current_index', -1)
        if current_tool == -1:
            return

        try:
            sensor_extruder = self.printer.lookup_object("filament_switch_sensor extruder_sensor", None)
            if sensor_extruder:
                # 检查断料助手和直接限位开关状态
                runout_helper_present = bool(sensor_extruder.runout_helper.filament_present)
                endstop_triggered = self._check_endstop_state('extruder_sensor')
                
                # 记录传感器状态用于调试（测试后移除）
                # logging.info(f"ACE 调试: runout_helper={runout_helper_present}, endstop={endstop_triggered}")
                
                # 如果线材不存在则检测到断料
                if not runout_helper_present or not endstop_triggered:
                    if not self.endless_spool_runout_detected:  # 仅触发一次
                        self.endless_spool_runout_detected = True
                        self.gcode.respond_info("ACE: 检测到自动续料断料，立即切换")
                        logging.info(f"ACE: 检测到断料 - runout_helper={runout_helper_present}, endstop={endstop_triggered}")
                        # 立即执行自动续料更换
                        self._execute_endless_spool_change()
        except Exception as e:
            logging.info(f'ACE: 断料检测错误: {str(e)}')

    def _execute_endless_spool_change(self):
        """执行自动续料工具更换 - 简化仅用于挤出机传感器"""
        if self.endless_spool_in_progress:
            return

        current_tool = self.variables.get('ace_current_index', -1)
        next_tool = self._find_next_available_slot(current_tool)
        
        if next_tool == -1:
            self.gcode.respond_info("ACE: 自动续料没有可用料盘，暂停打印")
            self.gcode.run_script_from_command('PAUSE')
            self.endless_spool_runout_detected = False
            return

        self.endless_spool_in_progress = True
        self.endless_spool_runout_detected = False
        
        self.gcode.respond_info(f"ACE: 自动续料从料盘 {current_tool} 切换到料盘 {next_tool}")
        
        # 在库存中将当前料盘标记为空
        if current_tool >= 0:
            self.inventory[current_tool] = {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
            # 将更新的库存保存到持久变量
            self.variables['ace_inventory'] = self.inventory
            self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
        
        try:
            # 直接自动续料更换 - 断料响应不需要工具更换宏
            
            # 步骤1：在空料盘上禁用进料辅助
            if current_tool != -1:
                self._disable_feed_assist(current_tool)
                self.wait_ace_ready()

            # 步骤2：从下一个料盘进料直到到达挤出机传感器
            sensor_extruder = self.printer.lookup_object("filament_switch_sensor extruder_sensor", None)
            
            # 从新料盘进料直到挤出机传感器触发
            self._feed(next_tool, self.toolchange_load_length, self.retract_speed)
            self.wait_ace_ready()

            # 等待线材到达挤出机传感器
            while not bool(sensor_extruder.runout_helper.filament_present):
                self.dwell(delay=0.1)

            if not bool(sensor_extruder.runout_helper.filament_present):
                raise ValueError("自动续料更换期间线材卡住")

            # 步骤3：为新料盘启用进料辅助
            self._enable_feed_assist(next_tool)

            # 步骤4：更新当前索引并保存状态
            self.variables['ace_current_index'] = next_tool
            self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_current_index VALUE=' + str(next_tool))
            
            self.endless_spool_in_progress = False
            
            self.gcode.respond_info(f"ACE: 自动续料完成，现在使用料盘 {next_tool}")
            
        except Exception as e:
            self.gcode.respond_info(f"ACE: 自动续料更换失败: {str(e)}")
            self.gcode.run_script_from_command('PAUSE')
            self.endless_spool_in_progress = False

    cmd_ACE_ENABLE_ENDLESS_SPOOL_help = '启用自动续料功能'

    cmd_ACE_ENABLE_ENDLESS_SPOOL_help = '启用自动续料功能'

    def cmd_ACE_ENABLE_ENDLESS_SPOOL(self, gcmd):
        self.endless_spool_enabled = True
        
        # 保存到持久变量
        self.variables['ace_endless_spool_enabled'] = True
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_endless_spool_enabled VALUE=True')
        
        gcmd.respond_info("ACE: 自动续料已启用（断料时立即切换，已保存到持久变量）")

    cmd_ACE_DISABLE_ENDLESS_SPOOL_help = '禁用自动续料功能'

    def cmd_ACE_DISABLE_ENDLESS_SPOOL(self, gcmd):
        self.endless_spool_enabled = False
        self.endless_spool_runout_detected = False
        self.endless_spool_in_progress = False
        
        # 保存到持久变量
        self.variables['ace_endless_spool_enabled'] = False
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=ace_endless_spool_enabled VALUE=False')
        
        gcmd.respond_info("ACE: 自动续料已禁用（已保存到持久变量）")

    cmd_ACE_ENDLESS_SPOOL_STATUS_help = '显示自动续料状态'

    def cmd_ACE_ENDLESS_SPOOL_STATUS(self, gcmd):
        status = self.get_status()['endless_spool']
        saved_enabled = self.variables.get('ace_endless_spool_enabled', False)
        
        gcmd.respond_info(f"ACE: 自动续料状态:")
        gcmd.respond_info(f"  - 当前已启用: {status['enabled']}")
        gcmd.respond_info(f"  - 保存的启用状态: {saved_enabled}")
        gcmd.respond_info(f"  - 模式: 检测到断料时立即切换")
        
        if status['enabled']:
            gcmd.respond_info(f"  - 检测到断料: {status['runout_detected']}")
            gcmd.respond_info(f"  - 进行中: {status['in_progress']}")

    def find_com_port(self, device_name):
        com_ports = serial.tools.list_ports.comports()
        for port, desc, hwid in com_ports:
            if device_name in desc:
                return port
        return None

    def cmd_ACE_DEBUG(self, gcmd):
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')

        try:
            def callback(self, response):
                self.gcode.respond_info(str(response))

            self.send_request(request = {"method": method, "params": json.loads(params)}, callback = callback)
        except Exception as e:
            self.gcode.respond_info('错误: ' + str(e))
        #self.gcode.respond_info(str(self.find_com_port('ACE')))


    def get_status(self, eventtime=None):
        status = self._info.copy()
        status['endless_spool'] = {
            'enabled': self.endless_spool_enabled,
            'runout_detected': self.endless_spool_runout_detected,
            'in_progress': self.endless_spool_in_progress
        }
        return status

    def cmd_ACE_SET_SLOT(self, gcmd):
        idx = gcmd.get_int('INDEX')
        if idx < 0 or idx >= 4:
            raise gcmd.error('无效的料盘索引')
        if gcmd.get_int('EMPTY', 0):
            self.inventory[idx] = {"status": "empty", "color": [0, 0, 0], "material": "", "temp": 0}
            # 保存到持久变量
            self.variables['ace_inventory'] = self.inventory
            self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
            gcmd.respond_info(f"料盘 {idx} 设置为空")
            return
        color_str = gcmd.get('COLOR', None)
        material = gcmd.get('MATERIAL', "")
        temp = gcmd.get_int('TEMP', 0)
        if not color_str or not material or temp <= 0:
            raise gcmd.error('除非 EMPTY=1，否则必须设置 COLOR、MATERIAL 和 TEMP')
        color = [int(x) for x in color_str.split(',')]
        if len(color) != 3:
            raise gcmd.error('COLOR 必须是 R,G,B')
        self.inventory[idx] = {
            "status": "ready",
            "color": color,
            "material": material,
            "temp": temp
        }
        # 保存到持久变量
        self.variables['ace_inventory'] = self.inventory
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
        gcmd.respond_info(f"料盘 {idx} 已设置: color={color}, material={material}, temp={temp}")

    def cmd_ACE_QUERY_SLOTS(self, gcmd):
        import json
        gcmd.respond_info(json.dumps(self.inventory))

    cmd_ACE_SAVE_INVENTORY_help = '手动将当前库存保存到持久存储'

    def cmd_ACE_SAVE_INVENTORY(self, gcmd):
        self.variables['ace_inventory'] = self.inventory
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_inventory VALUE=\'{json.dumps(self.inventory)}\'')
        gcmd.respond_info("ACE: 库存已保存到持久存储")

    cmd_ACE_TEST_RUNOUT_SENSOR_help = '测试并显示断料传感器状态'

    def cmd_ACE_TEST_RUNOUT_SENSOR(self, gcmd):
        try:
            sensor_extruder = self.printer.lookup_object("filament_switch_sensor extruder_sensor", None)
            if sensor_extruder:
                runout_helper_present = bool(sensor_extruder.runout_helper.filament_present)
                endstop_triggered = self._check_endstop_state('extruder_sensor')
                
                gcmd.respond_info(f"ACE: 挤出机传感器状态:")
                gcmd.respond_info(f"  - 断料助手线材存在: {runout_helper_present}")
                gcmd.respond_info(f"  - 限位开关触发: {endstop_triggered}")
                gcmd.respond_info(f"  - 自动续料已启用: {self.endless_spool_enabled}")
                gcmd.respond_info(f"  - 当前工具: {self.variables.get('ace_current_index', -1)}")
                gcmd.respond_info(f"  - 检测到断料: {self.endless_spool_runout_detected}")
                
                # 测试断料检测逻辑
                would_trigger = not runout_helper_present or not endstop_triggered
                gcmd.respond_info(f"  - 将触发断料: {would_trigger}")
            else:
                gcmd.respond_info("ACE: 未找到挤出机传感器")
        except Exception as e:
            gcmd.respond_info(f"ACE: 测试传感器错误: {str(e)}")

    cmd_ACE_GET_CURRENT_INDEX_help = '获取当前加载的料盘索引'

    def cmd_ACE_GET_CURRENT_INDEX(self, gcmd):
        current_index = self.variables.get('ace_current_index', -1)
        gcmd.respond_info(str(current_index))

    def _on_toolhead_move(self, event):
        """工具头移动的事件处理程序，用于监控挤出机移动"""
        if not self.endless_spool_enabled or self._park_in_progress or self.endless_spool_in_progress:
            return

        # 在任何移动期间检查断料
        self._endless_spool_runout_handler()
        
        # 如果检测到断料，跟踪挤出机距离
        if hasattr(event, 'newpos') and hasattr(event, 'oldpos'):
            newpos = event.newpos
            oldpos = event.oldpos
            if len(newpos) > 3 and len(oldpos) > 3:
                e_move = newpos[3] - oldpos[3]
                if e_move > 0 and self.endless_spool_runout_detected:
                    self._endless_spool_check_distance(e_move)

    cmd_ACE_CHANGE_SPOOL_help = '为特定索引更换耗材 - INDEX=（从管中回退线材，如果已加载则先卸载）'

    def cmd_ACE_CHANGE_SPOOL(self, gcmd):
        index = gcmd.get_int('INDEX', None)
        
        if index is None:
            raise gcmd.error('需要 INDEX 参数')
        
        if index < 0 or index >= 4:
            raise gcmd.error('错误的索引 - 必须是 0-3')
        
        gcmd.respond_info(f"ACE: 为索引 {index} 更换耗材")
        
        # 检查此料盘当前是否已加载（活动工具）
        current_tool = self.variables.get('ace_current_index', -1)
        
        if current_tool == index:
            # 如果这是当前加载的工具，先卸载它（T-1）
            gcmd.respond_info(f"ACE: 索引 {index} 当前已加载，先卸载...")
            # 创建适当的 gcode 命令来卸载工具
            unload_cmd = "ACE_CHANGE_TOOL TOOL=-1"
            self.gcode.run_script_from_command(unload_cmd)
            gcmd.respond_info("ACE: 工具已卸载")
        
        # 检查料盘是否非空（系统中已加载线材）
        slot_status = None
        if hasattr(self, '_info') and self._info and 'slots' in self._info:
            slot_status = self._info['slots'][index]['status']
        
        inventory_status = self.inventory[index]['status']
        
        # 如果料盘非空或系统中有线材，回退它
        if (slot_status and slot_status != 'empty') or (inventory_status and inventory_status != 'empty'):
            gcmd.respond_info(f"ACE: 从鲍登管回退索引 {index} 的线材")
            gcmd.respond_info(f"ACE: 以 {self.retract_speed}mm/min 回退 {self.bowden_tube_length}mm")
            
            try:
                self._retract(index, self.bowden_tube_length, self.retract_speed)
                gcmd.respond_info(f"ACE: 索引 {index} 的线材已回退")
            except Exception as e:
                gcmd.respond_info(f"ACE: 回退期间错误: {str(e)}")
                raise gcmd.error(f"回退线材失败: {str(e)}")
        else:
            gcmd.respond_info(f"ACE: 索引 {index} 已经为空，无需回退")
        
        gcmd.respond_info(f"ACE: 索引 {index} 的耗材更换完成")


def load_config(config):
    return BunnyAce(config)
