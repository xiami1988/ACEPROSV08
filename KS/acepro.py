import logging
import json
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango, Gdk, GLib
from ks_includes.screen_panel import ScreenPanel
from ks_includes.widgets.keypad import Keypad


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title)
        self.current_slot_settings = {"type": "PLA", "color": "255,255,255", "temp": "200"}
        self.ace_status = {}
        self.slot_inventory = []
        self.dryer_enabled = False
        self.current_loaded_slot = -1  # 缓存已加载的料盘
        self.numpad_visible = False  # 跟踪数字键盘状态
        self.endless_spool_enabled = False  # 跟踪自动续料状态
        
        # 初始化料盘组件列表
        self.slot_boxes = []
        self.slot_labels = []
        self.slot_color_boxes = []
        self.slot_buttons = []
        
        # 存储配置界面的实际料盘数据
        self.slot_data = [
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"},
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"},
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"},
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"}
        ]
        
        # 创建主屏幕布局
        self.create_main_screen()
        
        # 添加自定义 CSS 用于圆角框和颜色指示器
        self.add_custom_css()
        
        # 订阅 saved_variables 更新
        if hasattr(self._screen.printer, 'klippy') and hasattr(self._screen.printer.klippy, 'subscribe_object'):
            try:
                self._screen.printer.klippy.subscribe_object("saved_variables", ["variables"])
                logging.info("ACE: 已订阅 saved_variables 更新")
            except Exception as e:
                logging.error(f"ACE: 订阅 saved_variables 失败: {e}")
        
        # 从 saved_variables 初始化已加载料盘（将在 get_current_loaded_slot 中更新）
        
        # 立即尝试初始化当前已加载料盘
        self.initialize_loaded_slot()
    
    def add_custom_css(self):
        """添加自定义 CSS 用于料盘外观 - 使用特定的 ACE 类避免冲突"""
        css_provider = Gtk.CssProvider()
        css = """
        .ace_slot_color_indicator {
            border: 1px solid #333333;
            border-radius: 3px;
        }
        
        .ace_slot_button {
            border-radius: 10px;
            background-color: #2a2a2a;
            border: 2px solid #444444;
        }
        
        .ace_slot_button:hover {
            border-color: #666666;
        }
        
        .ace_slot_loaded {
            background-color: white;
            color: black;
        }
        
        .ace_slot_loaded .ace_slot_label {
            color: black;
        }
        
        .ace_slot_loaded .ace_slot_number {
            color: black;
        }
        
        .ace_slot_loaded * {
            color: black;
        }
        
        .ace_slot_empty {
            background-color: #2a2a2a;
            color: white;
        }
        
        .ace_slot_empty .ace_slot_label {
            color: white;
        }
        
        .ace_slot_empty .ace_slot_number {
            color: white;
            opacity: 0.8;
        }
        
        .ace_slot_number {
            font-size: 0.9em;
            opacity: 0.8;
        }
        
        .ace_slot_label {
            color: inherit;
        }
        
        .ace_color_preview {
            border: 2px solid #333333;
            border-radius: 5px;
            min-width: 20px;
            min-height: 15px;
        }
        
        .ace_numpad_button {
            background-color: #4a4a4a;
            color: white;
            border: 1px solid #666666;
            border-radius: 5px;
            font-size: 14px;
            font-weight: bold;
        }
        
        .ace_numpad_button:hover {
            background-color: #5a5a5a;
        }
        
        .ace_numpad_function {
            background-color: #666666;
            color: white;
        }
        """
        css_provider.load_from_data(css.encode())
        
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    
    def set_slot_color(self, color_box, rgb_color):
        """设置料盘颜色指示器的颜色"""
        r, g, b = rgb_color
        color = Gdk.RGBA(r/255.0, g/255.0, b/255.0, 1.0)
        color_box.override_background_color(Gtk.StateFlags.NORMAL, color)
    
    def get_current_loaded_slot(self):
        """获取当前已加载的料盘"""
        try:
            # 首先尝试从打印机数据获取
            if hasattr(self._screen, 'printer') and hasattr(self._screen.printer, 'data'):
                printer_data = self._screen.printer.data
                
                # 检查 saved_variables
                if 'saved_variables' in printer_data:
                    save_vars = printer_data['saved_variables']
                    
                    if isinstance(save_vars, dict) and 'variables' in save_vars:
                        variables = save_vars['variables']
                        
                        if 'ace_current_index' in variables:
                            value = int(variables['ace_current_index'])
                            self.current_loaded_slot = value
                            return value
            
            # 返回缓存值或默认值
            return getattr(self, 'current_loaded_slot', -1)
            
        except Exception as e:
            logging.error(f"ACE: 读取 ace_current_index 错误: {e}")
            return getattr(self, 'current_loaded_slot', -1)
    
    def initialize_loaded_slot(self):
        """从 saved_variables 或查询 ACE 状态初始化已加载料盘"""
        # 尝试获取当前已加载料盘
        current_slot = self.get_current_loaded_slot()
        
        # 如果仍然没有有效料盘，查询 ACE 当前状态
        if current_slot == -1:
            logging.info("ACE: 未找到已加载料盘，将查询 ACE 状态")
            # 查询 ACE 当前已加载料盘索引
            if hasattr(self._screen, '_ws') and hasattr(self._screen._ws, 'klippy'):
                self._screen._ws.klippy.gcode_script("ACE_GET_CURRENT_INDEX")
                logging.info("ACE: 已发送 ACE_GET_CURRENT_INDEX 命令")
    
    def show_number_input(self, title, message, current_value, min_val, max_val, callback):
        """使用紧凑的自定义数字键盘显示数字输入对话框"""
        # 创建非常紧凑的自定义数字键盘以适应对话框
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_margin_left(5)
        vbox.set_margin_right(5)
        vbox.set_margin_top(5)
        vbox.set_margin_bottom(5)
        
        # 存储回调和约束
        self.temp_input_callback = callback
        self.temp_min = min_val
        self.temp_max = max_val
        
        # 紧凑标题
        title_label = Gtk.Label(label=f"{message} ({min_val}-{max_val})")
        title_label.get_style_context().add_class("description")
        vbox.pack_start(title_label, False, False, 0)
        
        # 带关闭按钮的输入字段
        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        self.temp_entry = Gtk.Entry()
        self.temp_entry.set_text(str(current_value))
        self.temp_entry.set_halign(Gtk.Align.CENTER)
        self.temp_entry.set_size_request(100, 30)
        entry_box.pack_start(self.temp_entry, True, True, 0)
        
        # 关闭按钮
        close_btn = self._gtk.Button("cancel", scale=0.6)
        close_btn.set_size_request(30, 30)
        close_btn.connect("clicked", self.close_temp_dialog)
        entry_box.pack_start(close_btn, False, False, 0)
        
        vbox.pack_start(entry_box, False, False, 2)
        
        # 紧凑数字网格（更小的按钮）
        numpad = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
        numpad.set_row_spacing(2)
        numpad.set_column_spacing(2)
        
        # 数字按钮 1-9, 0, 退格, 小数点
        buttons = [
            ['1', '2', '3'],
            ['4', '5', '6'], 
            ['7', '8', '9'],
            ['⌫', '0', '.']
        ]
        
        for row, button_row in enumerate(buttons):
            for col, btn_text in enumerate(button_row):
                btn = Gtk.Button(label=btn_text)
                btn.set_size_request(50, 35)  # 小型紧凑按钮
                btn.get_style_context().add_class("numpad_key")
                if btn_text == '⌫':
                    btn.connect("clicked", self.numpad_backspace)
                else:
                    btn.connect("clicked", self.numpad_clicked, btn_text)
                numpad.attach(btn, col, row, 1, 1)
        
        vbox.pack_start(numpad, False, False, 2)
        
        # 确定按钮
        ok_btn = self._gtk.Button("complete", "确定", "color1")
        ok_btn.set_size_request(-1, 35)
        ok_btn.connect("clicked", self.handle_temp_ok)
        vbox.pack_start(ok_btn, False, False, 2)
        
        # 创建无额外按钮的对话框
        buttons = []
        
        def response_callback(dialog, response_id):
            self.temp_input_dialog = None
        
        self.temp_input_dialog = self._gtk.Dialog(title, buttons, vbox, response_callback)
    
    def numpad_clicked(self, widget, digit):
        """处理数字按钮点击"""
        current = self.temp_entry.get_text()
        self.temp_entry.set_text(current + digit)
    
    def numpad_backspace(self, widget):
        """处理退格按钮"""
        current = self.temp_entry.get_text()
        if len(current) > 0:
            self.temp_entry.set_text(current[:-1])
    
    def handle_temp_ok(self, widget):
        """处理确定按钮点击"""
        try:
            value = int(float(self.temp_entry.get_text()))
            if self.temp_min <= value <= self.temp_max:
                self.temp_input_callback(value)
                self.close_temp_dialog()
            else:
                self._screen.show_popup_message(f"数值必须在 {self.temp_min}-{self.temp_max} 之间")
        except (ValueError, TypeError):
            self._screen.show_popup_message("无效数字")
    
    def close_temp_dialog(self, widget=None):
        """关闭温度输入对话框"""
        if hasattr(self, 'temp_input_dialog') and self.temp_input_dialog:
            if hasattr(self._gtk, 'remove_dialog'):
                self._gtk.remove_dialog(self.temp_input_dialog)
            self.temp_input_dialog = None
    
    def show_color_picker(self, title, current_color, callback):
        """显示非常紧凑的颜色选择器对话框"""
        # 存储颜色选择器的回调
        self.color_picker_callback = callback
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)  # 最小间距
        main_box.set_margin_left(10)  # 最小边距
        main_box.set_margin_right(10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)
        
        # 当前颜色值
        self.picker_rgb = list(current_color)
        
        # 单行显示预览和 RGB 显示
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        # 颜色预览
        self.color_preview_widget = Gtk.EventBox()
        self.color_preview_widget.get_style_context().add_class("ace_color_preview")
        self.color_preview_widget.set_size_request(60, 30)  # 更小的预览
        self.set_color_preview(self.color_preview_widget, self.picker_rgb)
        top_row.pack_start(self.color_preview_widget, False, False, 0)
        
        # RGB 值显示
        self.rgb_label_widget = Gtk.Label(label=f"RGB: {self.picker_rgb[0]},{self.picker_rgb[1]},{self.picker_rgb[2]}")
        self.rgb_label_widget.set_halign(Gtk.Align.START)
        top_row.pack_start(self.rgb_label_widget, True, True, 0)
        
        main_box.pack_start(top_row, False, False, 0)
        
        # 非常紧凑的滑块 - 水平布局
        sliders_grid = Gtk.Grid()
        sliders_grid.set_row_spacing(3)
        sliders_grid.set_column_spacing(5)
        
        # 创建 RGB 滑块并引用更新函数
        self.create_mini_slider("红", 0, 0, sliders_grid)
        self.create_mini_slider("绿", 1, 1, sliders_grid)
        self.create_mini_slider("蓝", 2, 2, sliders_grid)
        
        main_box.pack_start(sliders_grid, False, False, 0)
        
        # 最小预设颜色 - 单行
        #presets_label = Gtk.Label(label="预设:")
        #presets_label.set_halign(Gtk.Align.START)
        #main_box.pack_start(presets_label, False, False, 0)
        
        # 仅6种最常见颜色在一行
        preset_colors = [
            ("白", [255, 255, 255]),
            ("黑", [0, 0, 0]),
            ("红", [255, 0, 0]),
            ("绿", [0, 255, 0]),
            ("蓝", [0, 0, 255]),
            ("黄", [255, 255, 0])
        ]
        
        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        preset_row.set_homogeneous(True)
        
        for name, rgb in preset_colors:
            preset_btn = Gtk.Button()
            preset_btn.set_size_request(35, 25)  # 非常小的按钮
            preset_btn.set_label(name)
            
            # 设置按钮颜色
            preset_color = Gdk.RGBA(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0, 1.0)
            preset_btn.override_background_color(Gtk.StateFlags.NORMAL, preset_color)
            
            # 设置文本颜色
            brightness = (rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114)
            text_color = Gdk.RGBA(0, 0, 0, 1) if brightness > 128 else Gdk.RGBA(1, 1, 1, 1)
            preset_btn.override_color(Gtk.StateFlags.NORMAL, text_color)
            
            def on_preset_click(widget, preset_rgb):
                self.picker_rgb[:] = preset_rgb
                self.update_color_preview()
            
            preset_btn.connect("clicked", on_preset_click, rgb[:])
            preset_row.pack_start(preset_btn, True, True, 0)
        
        main_box.pack_start(preset_row, False, False, 0)
        
        buttons = [
            {"name": "取消", "response": Gtk.ResponseType.CANCEL},
            {"name": "确定", "response": Gtk.ResponseType.OK}
        ]
        
        # 使用 KlipperScreen 对话框方法
        self._gtk.Dialog(title, buttons, main_box, self.color_picker_response)
    
    def create_mini_slider(self, color_name, color_index, row, grid):
        """为 RGB 颜色分量创建迷你滑块"""
        # 标签
        label = Gtk.Label(label=f"{color_name}:")
        label.set_size_request(25, -1)
        grid.attach(label, 0, row, 1, 1)
        
        # 值标签
        value_label = Gtk.Label(label=str(self.picker_rgb[color_index]))
        value_label.set_size_request(30, -1)
        value_label.set_halign(Gtk.Align.END)
        grid.attach(value_label, 1, row, 1, 1)
        
        # 滑块
        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
        slider.set_value(self.picker_rgb[color_index])
        slider.set_size_request(150, 20)  # 紧凑滑块
        slider.set_draw_value(False)
        
        def on_slider_change(widget):
            value = int(widget.get_value())
            self.picker_rgb[color_index] = value
            value_label.set_text(str(value))
            self.update_color_preview()
        
        slider.connect("value-changed", on_slider_change)
        grid.attach(slider, 2, row, 1, 1)
    
    def update_color_preview(self):
        """更新颜色选择器中的颜色预览"""
        self.set_color_preview(self.color_preview_widget, self.picker_rgb)
        self.rgb_label_widget.set_text(f"RGB: {self.picker_rgb[0]},{self.picker_rgb[1]},{self.picker_rgb[2]}")
    
    def color_picker_response(self, dialog, response_id):
        """处理颜色选择器对话框响应"""
        logging.info(f"ACE: 颜色选择器响应: {response_id}")
        try:
            if response_id == Gtk.ResponseType.OK:
                logging.info(f"ACE: 颜色选择器确定点击, RGB: {self.picker_rgb}")
                if self.color_picker_callback:
                    self.color_picker_callback(self.picker_rgb[:])
            else:
                logging.info("ACE: 颜色选择器已取消")
        finally:
            # 通过移除对话框确保对话框关闭
            if hasattr(self._gtk, 'remove_dialog') and dialog:
                self._gtk.remove_dialog(dialog)
                logging.info("ACE: 颜色选择器对话框已关闭")
    
    def set_color_preview(self, widget, rgb_color):
        """设置颜色预览小部件背景"""
        r, g, b = rgb_color
        color = Gdk.RGBA(r/255.0, g/255.0, b/255.0, 1.0)
        widget.override_background_color(Gtk.StateFlags.NORMAL, color)
    
    def update_slot_loaded_states(self):
        """基于 ace_current_index 更新所有料盘加载状态"""
        current_loaded = self.get_current_loaded_slot()
        
        logging.info(f"ACE: 当前已加载料盘: {current_loaded}")
        
        for slot in range(4):
            slot_btn = self.slot_buttons[slot]
            if slot == current_loaded:
                slot_btn.get_style_context().remove_class("ace_slot_empty")
                slot_btn.get_style_context().add_class("ace_slot_loaded")
            else:
                slot_btn.get_style_context().remove_class("ace_slot_loaded")
                slot_btn.get_style_context().add_class("ace_slot_empty")
        
        # 更新状态标签
        if current_loaded != -1:
            self.status_label.set_text(f"ACE: 就绪 - 料盘 {current_loaded} 已加载")
        else:
            self.status_label.set_text("ACE: 就绪")
    
    def on_endless_spool_toggled(self, switch, state):
        """处理自动续料开关切换"""
        self.endless_spool_enabled = state
        
        # 发送命令到 ACE 系统启用/禁用自动续料
        if state:
            self._screen._ws.klippy.gcode_script("ACE_ENABLE_ENDLESS_SPOOL")
            self._screen.show_popup_message("自动续料已启用", 1)
            logging.info("ACE: 自动续料已启用")
        else:
            self._screen._ws.klippy.gcode_script("ACE_DISABLE_ENDLESS_SPOOL")
            self._screen.show_popup_message("自动续料已禁用", 1)
            logging.info("ACE: 自动续料已禁用")
    
    def on_slot_clicked(self, widget, slot):
        """处理料盘按钮点击"""
        current_loaded = self.get_current_loaded_slot()
        
        if current_loaded == slot:
            # 点击已加载料盘 - 询问卸载
            self.show_unload_confirmation(slot)
        else:
            # 点击未加载料盘 - 询问加载
            self.show_load_confirmation(slot)
    
    def show_load_confirmation(self, slot):
        """显示加载料盘的确认对话框"""
        slot_info = self.slot_labels[slot].get_text()
        if slot_info == "空":
            self._screen.show_popup_message("料盘为空。请先使用设置按钮进行配置。")
            return
        
        current_loaded = self.get_current_loaded_slot()
        message = f"加载料盘 {slot}？\n\n{slot_info}"
        if current_loaded != -1:
            message += f"\n\n这将卸载料盘 {current_loaded}"
        
        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        
        buttons = [
            {"name": "取消", "response": Gtk.ResponseType.CANCEL},
            {"name": "加载", "response": Gtk.ResponseType.OK}
        ]
        
        def load_response(dialog, response_id):
            try:
                if response_id == Gtk.ResponseType.OK:
                    # 立即更新缓存值以实现响应式 UI
                    self.current_loaded_slot = slot
                    self.update_slot_loaded_states()
                    
                    # 发送实际命令
                    self._screen._ws.klippy.gcode_script(f"ACE_CHANGE_TOOL TOOL={slot}")
                    self._screen.show_popup_message(f"正在加载料盘 {slot}...", 1)
                    
                    # 如果处于配置模式，则返回主屏幕
                    if hasattr(self, 'current_config_slot'):
                        self.return_to_main_screen()
                elif response_id == Gtk.ResponseType.CANCEL:
                    # 只需关闭对话框 - 无需操作
                    logging.info(f"ACE: 用户取消了加载料盘 {slot}")
            finally:
                # 确保对话框关闭
                if hasattr(self._gtk, 'remove_dialog') and dialog:
                    self._gtk.remove_dialog(dialog)
        
        self._gtk.Dialog(f"加载料盘 {slot}", buttons, label, load_response)
    
    def show_unload_confirmation(self, slot):
        """显示卸载料盘的确认对话框"""
        slot_info = self.slot_labels[slot].get_text()
        message = f"卸载料盘 {slot}？\n\n{slot_info}"
        
        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        
        buttons = [
            {"name": "取消", "response": Gtk.ResponseType.CANCEL},
            {"name": "卸载", "response": Gtk.ResponseType.OK}
        ]
        
        def unload_response(dialog, response_id):
            try:
                if response_id == Gtk.ResponseType.OK:
                    # 立即更新缓存值以实现响应式 UI
                    self.current_loaded_slot = -1
                    self.update_slot_loaded_states()
                    
                    # 发送实际命令
                    self._screen._ws.klippy.gcode_script(f"ACE_CHANGE_TOOL TOOL=-1")
                    self._screen.show_popup_message(f"正在卸载料盘 {slot}...", 1)
                elif response_id == Gtk.ResponseType.CANCEL:
                    # 只需关闭对话框 - 无需操作
                    logging.info(f"ACE: 用户取消了卸载料盘 {slot}")
            finally:
                # 确保对话框关闭
                if hasattr(self._gtk, 'remove_dialog') and dialog:
                    self._gtk.remove_dialog(dialog)
        
        self._gtk.Dialog(f"卸载料盘 {slot}", buttons, label, unload_response)
    
    def activate(self):
        """面板显示时调用"""
        logging.info("ACE: 面板已激活")
        
        # 再次尝试从 saved_variables 初始化已加载料盘
        self.initialize_loaded_slot()
        
        # 更新状态，将查询 ACE 并更新显示
        self.update_status()
    
    def delayed_init(self):
        """延迟初始化以允许 save_variables 加载"""
        logging.info("ACE: 延迟初始化调用")
        current_slot = self.get_current_loaded_slot()
        if current_slot != -1:
            logging.info(f"ACE: 延迟初始化找到料盘: {current_slot}")
            self.update_slot_loaded_states()
        return False  # 不重复超时
    
    def refresh_status(self, widget):
        """手动刷新按钮"""
        # 查询 ACE 数据
        self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
        
        # 查询当前加载索引
        self._screen._ws.klippy.gcode_script("ACE_GET_CURRENT_INDEX")
        
        # 更新加载状态
        self.update_slot_loaded_states()
        self._screen.show_popup_message("正在刷新耗材数据...", 1)
    
    def show_slot_settings(self, widget, slot):
        """显示双列料盘配置屏幕"""
        self.current_config_slot = slot
        self.show_slot_config_screen(slot)
    
    def show_slot_config_screen(self, slot):
        """创建紧凑的双列配置屏幕以适应 480px"""
        # 从料盘数据加载当前值
        slot_info = self.slot_data[slot]
        self.config_material = slot_info["material"]
        self.config_color = slot_info["color"][:]  # 复制颜色数组
        self.config_temp = slot_info["temp"]
        
        logging.info(f"ACE: 加载料盘 {slot} 配置 - 材料: {self.config_material}, 颜色: {self.config_color}, 温度: {self.config_temp}")
        
        # 清除当前内容并创建双列布局
        for child in self.content.get_children():
            self.content.remove(child)
        
        # 创建主网格，两列 - 紧凑间距
        main_grid = Gtk.Grid()
        main_grid.set_column_homogeneous(True)
        main_grid.set_row_spacing(2)  # 从 10 减少
        main_grid.set_column_spacing(1)  # 从 10 减少
        main_grid.set_margin_left(5)  # 从 15 减少
        main_grid.set_margin_right(10)
        main_grid.set_margin_top(2)   # 从 15 减少
        main_grid.set_margin_bottom(1)
        
        # 左列 - 配置选项（紧凑）
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)  # 从 15 减少
        
        # 左列紧凑标题
        config_title = Gtk.Label(label=f"配置料盘 {slot}")
        config_title.get_style_context().add_class("description")  # 比 temperature_entry 小
        left_box.pack_start(config_title, False, False, 0)
        
        # 材料选择按钮 - 更小，带当前值
        self.material_btn = self._gtk.Button("filament", f"材料: {self.config_material}", "color1")
        self.material_btn.set_size_request(-1, 45)  # 从 60 减少
        self.material_btn.connect("clicked", self.show_material_selection)
        left_box.pack_start(self.material_btn, False, False, 0)
        
        # 颜色选择按钮带预览 - 更小，带当前颜色
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)  # 从 10 减少
        
        # 更小的颜色预览带当前颜色
        self.config_color_preview = Gtk.EventBox()
        self.config_color_preview.set_size_request(30, 30)  # 从 40x40 减少
        self.config_color_preview.get_style_context().add_class("ace_color_preview")
        self.set_color_preview(self.config_color_preview, self.config_color)
        color_box.pack_start(self.config_color_preview, False, False, 0)
        
        # 更小的颜色按钮
        self.color_btn = self._gtk.Button("palette", "选择颜色", "color2")
        self.color_btn.set_size_request(-1, 45)  # 从 60 减少
        self.color_btn.connect("clicked", self.show_color_selection)
        color_box.pack_start(self.color_btn, True, True, 0)
        
        left_box.pack_start(color_box, False, False, 0)
        
        # 温度选择按钮 - 更小，带当前值
        self.temp_btn = self._gtk.Button("heat-up", f"温度: {self.config_temp}°C", "color3")
        self.temp_btn.set_size_request(-1, 45)  # 从 60 减少
        self.temp_btn.connect("clicked", self.show_temperature_selection)
        left_box.pack_start(self.temp_btn, False, False, 0)
        
        # 紧凑操作按钮
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)  # 从 10 减少
        action_box.set_homogeneous(True)
        
        # 更小的保存按钮
        save_btn = self._gtk.Button("complete", "保存", "color1")
        save_btn.set_size_request(-1, 40)  # 从 50 减少
        save_btn.connect("clicked", self.save_slot_config)
        action_box.pack_start(save_btn, True, True, 0)
        
        # 更小的取消按钮
        cancel_btn = self._gtk.Button("cancel", "取消", "color4")
        cancel_btn.set_size_request(-1, 40)  # 从 50 减少
        cancel_btn.connect("clicked", self.cancel_slot_config)
        action_box.pack_start(cancel_btn, True, True, 0)
        
        left_box.pack_end(action_box, False, False, 0)
        
        # 右列 - 选择面板（紧凑）
        self.right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)  # 从 10 减少
        
        # 右列紧凑欢迎消息
        welcome_label = Gtk.Label(label="从左侧选择一个选项\n来配置料盘")
        welcome_label.set_justify(Gtk.Justification.CENTER)
        welcome_label.get_style_context().add_class("description")
        self.right_box.pack_start(welcome_label, True, True, 0)
        
        # 添加列到主网格
        main_grid.attach(left_box, 0, 0, 1, 1)
        main_grid.attach(self.right_box, 1, 0, 1, 1)
        
        self.content.add(main_grid)
        self.content.show_all()
    
    def show_material_selection(self, widget):
        """在右列显示紧凑的材料选择"""
        # 清除右列
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        # 紧凑材料选择标题
        title = Gtk.Label(label="选择材料")
        title.get_style_context().add_class("description")  # 更小标题
        self.right_box.pack_start(title, False, False, 0)
        
        # 紧凑材料列表
        materials = ["PLA", "ABS", "PETG", "TPU", "ASA", "PVA", "HIPS", "PC"]
        
        for material in materials:
            material_btn = self._gtk.Button("filament", material, "color2")
            material_btn.set_size_request(-1, 35)  # 从 50 减少
            material_btn.connect("clicked", self.select_material, material)
            
            # 高亮当前选择
            if material == self.config_material:
                material_btn.get_style_context().add_class("button_active")
            
            self.right_box.pack_start(material_btn, False, False, 3)  # 减少间距
        
        self.right_box.show_all()
    
    def select_material(self, widget, material):
        """处理材料选择"""
        self.config_material = material
        self.material_btn.set_label(f"材料: {material}")
        
        # 清除右列回到欢迎消息
        self.clear_right_column()
    
    def show_color_selection(self, widget):
        """在右列显示紧凑的颜色选择器"""
        # 清除右列
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        # 紧凑颜色选择标题
        #title = Gtk.Label(label="选择颜色")
        #title.get_style_context().add_class("description")
        #self.right_box.pack_start(title, False, False, 0)
        
        # 紧凑当前颜色预览和 RGB 显示
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)  # 减少间距
        preview_box.set_halign(Gtk.Align.CENTER)
        
        self.right_color_preview = Gtk.EventBox()
        self.right_color_preview.set_size_request(40, 40)  # 更小预览
        self.right_color_preview.get_style_context().add_class("ace_color_preview")
        self.set_color_preview(self.right_color_preview, self.config_color)
        preview_box.pack_start(self.right_color_preview, False, False, 0)
        
        self.rgb_display = Gtk.Label(label=f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")
        self.rgb_display.get_style_context().add_class("description")
        preview_box.pack_start(self.rgb_display, False, False, 0)
        
        self.right_box.pack_start(preview_box, False, False, 5)  # 减少边距
        
        # 紧凑 RGB 滑块
        slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)  # 减少间距
        
        self.color_sliders = {}
        for i, color_name in enumerate(['红', '绿', '蓝']):
            color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)  # 减少间距
            
            label = Gtk.Label(label=f"{color_name[0]}:")  # 仅首字母
            label.set_size_request(20, -1)  # 更小标签
            color_row.pack_start(label, False, False, 0)
            
            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            slider.set_value(self.config_color[i])
            slider.set_size_request(150, 25)  # 更小滑块
            slider.set_draw_value(True)
            slider.set_value_pos(Gtk.PositionType.RIGHT)
            slider.connect("value-changed", self.on_color_slider_changed, i)
            self.color_sliders[i] = slider
            color_row.pack_start(slider, True, True, 0)
            
            slider_box.pack_start(color_row, False, False, 0)
        
        self.right_box.pack_start(slider_box, False, False, 5)
        
        # 紧凑颜色预设
        #presets_label = Gtk.Label(label="预设")
        #presets_label.get_style_context().add_class("description")
        #self.right_box.pack_start(presets_label, False, False, 3)
        
        preset_colors = [
            ("白色", [255, 255, 255]),
            ("黑色", [0, 0, 0]),
            ("红色", [255, 0, 0]),
            ("绿色", [0, 255, 0]),
            ("蓝色", [0, 0, 255]),
            ("黄色", [255, 255, 0])
        ]
        
        preset_grid = Gtk.Grid()
        preset_grid.set_row_spacing(3)  # 减少间距
        preset_grid.set_column_spacing(3)
        preset_grid.set_halign(Gtk.Align.CENTER)
        
        for i, (name, rgb) in enumerate(preset_colors):
            preset_btn = Gtk.Button(label=name)
            preset_btn.set_size_request(60, 25)  # 更小的按钮
            
            # 设置按钮颜色
            color = Gdk.RGBA(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0, 1.0)
            preset_btn.override_background_color(Gtk.StateFlags.NORMAL, color)
            
            # 基于亮度设置文本颜色
            brightness = (rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114)
            text_color = Gdk.RGBA(0, 0, 0, 1) if brightness > 128 else Gdk.RGBA(1, 1, 1, 1)
            preset_btn.override_color(Gtk.StateFlags.NORMAL, text_color)
            
            preset_btn.connect("clicked", self.select_color_preset, rgb[:])
            preset_grid.attach(preset_btn, i % 3, i // 3, 1, 1)  # 3列而不是4
        
        self.right_box.pack_start(preset_grid, False, False, 5)
        
        # 紧凑应用颜色按钮
        apply_btn = self._gtk.Button("complete", "应用颜色", "color1")
        apply_btn.set_size_request(-1, 30)  # 更小按钮
        apply_btn.connect("clicked", self.apply_color_selection)
        self.right_box.pack_end(apply_btn, False, False, 0)
        
        self.right_box.show_all()
    
    def on_color_slider_changed(self, slider, color_index):
        """处理颜色滑块变化"""
        value = int(slider.get_value())
        self.config_color[color_index] = value
        
        # 更新预览和 RGB 显示
        self.set_color_preview(self.right_color_preview, self.config_color)
        self.rgb_display.set_text(f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")
    
    def select_color_preset(self, widget, rgb):
        """处理颜色预设选择"""
        self.config_color = rgb[:]
        
        # 更新滑块
        for i, value in enumerate(rgb):
            self.color_sliders[i].set_value(value)
        
        # 更新预览和 RGB 显示
        self.set_color_preview(self.right_color_preview, self.config_color)
        self.rgb_display.set_text(f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")
    
    def apply_color_selection(self, widget):
        """应用选定的颜色"""
        self.set_color_preview(self.config_color_preview, self.config_color)
        self.clear_right_column()
    
    def show_temperature_selection(self, widget):
        """使用类似 temperature.py 的 Keypad 显示温度选择"""
        # 清除右列
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        # 温度选择标题
        title = Gtk.Label(label="设置温度")
        title.get_style_context().add_class("temperature_entry")
        self.right_box.pack_start(title, False, False, 0)
        
        # 创建与 temperature.py 完全相同的键盘小部件
        if not hasattr(self, 'config_keypad'):
            self.config_keypad = Keypad(
                self._screen,
                self.handle_temperature_input,
                None,  # 无 PID 校准
                self.clear_right_column,  # 关闭回调
            )
        
        # 设置当前温度值
        self.config_keypad.clear()
        self.config_keypad.labels['entry'].set_text(str(self.config_temp))
        
        # 隐藏 PID 按钮
        self.config_keypad.show_pid(False)
        
        # 添加键盘到右列
        self.right_box.pack_start(self.config_keypad, True, True, 0)
        
        self.right_box.show_all()
    
    def handle_temperature_input(self, temp):
        """处理来自键盘的温度输入"""
        try:
            temp_value = int(float(temp))
            if 0 <= temp_value <= 300:
                self.config_temp = temp_value
                self.temp_btn.set_label(f"温度: {temp_value}°C")
                self.clear_right_column()
            else:
                self._screen.show_popup_message("温度必须在 0-300°C 之间")
        except (ValueError, TypeError):
            self._screen.show_popup_message("无效的温度值")
    
    def clear_right_column(self, widget=None):
        """清除右列并显示欢迎消息"""
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        welcome_label = Gtk.Label(label="从左侧选择一个选项\n来配置料盘")
        welcome_label.set_justify(Gtk.Justification.CENTER)
        welcome_label.get_style_context().add_class("description")
        self.right_box.pack_start(welcome_label, True, True, 0)
        
        self.right_box.show_all()
    
    def save_slot_config(self, widget):
        """保存料盘配置"""
        slot = self.current_config_slot
        material = self.config_material
        color = f"{self.config_color[0]},{self.config_color[1]},{self.config_color[2]}"
        temp = self.config_temp
        
        # 更新存储的料盘数据
        self.slot_data[slot] = {
            "material": material,
            "color": self.config_color[:],  # 复制颜色数组
            "temp": temp,
            "status": "ready"
        }
        
        # 立即更新料盘显示
        self.slot_labels[slot].set_text(f"{material} {temp}°C")
        self.set_slot_color(self.slot_color_boxes[slot], self.config_color)
        
        # 发送 ACE_SET_SLOT 命令
        cmd = f"ACE_SET_SLOT INDEX={slot} COLOR={color} MATERIAL={material} TEMP={temp}"
        self._screen._ws.klippy.gcode_script(cmd)
        
        self._screen.show_popup_message(f"料盘 {slot} 已配置: {material} {temp}°C", 1)
        
        # 刷新数据并返回主屏幕
        self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
        self.return_to_main_screen()
    
    def cancel_slot_config(self, widget):
        """取消配置并返回主屏幕"""
        self.return_to_main_screen()
    
    def return_to_main_screen(self):
        """返回主 ACE 面板屏幕"""
        # 清除内容并重新创建主屏幕
        for child in self.content.get_children():
            self.content.remove(child)
        
        # 重新创建主 ACE 面板布局
        self.create_main_screen()
    
    def create_main_screen(self):
        """创建主 ACE 面板屏幕布局"""
        # 创建主容器
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.set_margin_left(15)
        main_box.set_margin_right(15)
        main_box.set_margin_top(15)
        main_box.set_margin_bottom(15)
        
        # 顶部行，带状态和自动续料开关
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        # ACE 状态显示
        self.status_label = Gtk.Label(label="ACE: 就绪")
        self.status_label.get_style_context().add_class("temperature_entry")
        self.status_label.set_size_request(-1, 40)
        self.status_label.set_halign(Gtk.Align.START)
        top_row.pack_start(self.status_label, True, True, 0)
        
        # 自动续料开关部分（右上角）
        endless_spool_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        endless_spool_box.set_halign(Gtk.Align.END)
        
        # 自动续料标签
        endless_label = Gtk.Label(label="自动续料:")
        endless_label.get_style_context().add_class("description")
        endless_spool_box.pack_start(endless_label, False, False, 0)
        
        # 自动续料开关
        self.endless_spool_switch = Gtk.Switch()
        self.endless_spool_switch.set_active(self.endless_spool_enabled)
        self.endless_spool_switch.connect("state-set", self.on_endless_spool_toggled)
        endless_spool_box.pack_start(self.endless_spool_switch, False, False, 0)
        
        top_row.pack_end(endless_spool_box, False, False, 0)
        main_box.pack_start(top_row, False, False, 0)
        
        # 料盘容器 - 水平布局
        slots_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        slots_box.set_homogeneous(True)
        
        self.slot_boxes = []
        self.slot_labels = []
        self.slot_color_boxes = []
        self.slot_buttons = []
        
        for slot in range(4):
            # 创建可点击的料盘按钮（高25%）
            slot_btn = Gtk.Button()
            slot_btn.get_style_context().add_class("ace_slot_button")  # 更具体的类
            slot_btn.set_relief(Gtk.ReliefStyle.NONE)
            slot_btn.set_size_request(-1, 125)  # 高25%（原约100px，现125px）
            slot_btn.connect("clicked", self.on_slot_clicked, slot)
            
            # 料盘内容框
            slot_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            slot_content.set_margin_left(8)
            slot_content.set_margin_right(8)
            slot_content.set_margin_top(10)  # 稍多顶部边距
            slot_content.set_margin_bottom(10)  # 稍多底部边距
            
            # 顶部行：颜色指示器和状态
            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            
            # 颜色矩形
            color_box = Gtk.EventBox()
            color_box.set_size_request(20, 20)
            color_box.get_style_context().add_class("ace_slot_color_indicator")  # 更具体的类
            # 默认黑色
            self.set_slot_color(color_box, [0, 0, 0])
            top_row.pack_start(color_box, False, False, 0)
            self.slot_color_boxes.append(color_box)
            
            # 料盘标签
            slot_label = Gtk.Label(label="空")
            slot_label.set_ellipsize(Pango.EllipsizeMode.END)  # 修正: END 而不是 End
            slot_label.set_halign(Gtk.Align.START)
            slot_label.get_style_context().add_class("ace_slot_label")  # 更具体的类
            top_row.pack_start(slot_label, True, True, 0)
            self.slot_labels.append(slot_label)
            
            slot_content.pack_start(top_row, True, True, 0)
            
            # 料盘编号标签
            slot_num_label = Gtk.Label(label=f"料盘 {slot}")
            slot_num_label.get_style_context().add_class("ace_slot_number")  # 更具体的类
            slot_content.pack_start(slot_num_label, False, False, 0)
            
            slot_btn.add(slot_content)
            slots_box.pack_start(slot_btn, True, True, 0)
            self.slot_buttons.append(slot_btn)
        
        main_box.pack_start(slots_box, False, False, 0)
        
        # 设置齿轮行 - 在料盘框下方（更小，更近）
        settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        settings_box.set_homogeneous(True)
        settings_box.set_margin_top(5)  # 更靠近料盘框
        
        for slot in range(4):
            # 创建容器以居中较小的按钮
            settings_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            settings_container.set_halign(Gtk.Align.CENTER)
            
            settings_btn = self._gtk.Button("settings", "", "color2")
            settings_btn.set_size_request(36, 27)  # 小10%（原40x30，现36x27）
            settings_btn.connect("clicked", self.show_slot_settings, slot)
            settings_btn.set_tooltip_text(f"配置料盘 {slot}")
            
            settings_container.pack_start(settings_btn, False, False, 0)
            settings_box.pack_start(settings_container, True, True, 0)
        
        main_box.pack_start(settings_box, False, False, 0)
        
        # 底部行 - 刷新和干燥器按钮
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        bottom_box.set_homogeneous(True)
        
        # 刷新按钮
        refresh_btn = self._gtk.Button("refresh", "刷新耗材数据", "color3")
        refresh_btn.set_size_request(-1, 50)
        refresh_btn.connect("clicked", self.refresh_status)
        bottom_box.pack_start(refresh_btn, True, True, 0)
        
        # 干燥器切换按钮
        self.dryer_btn = self._gtk.Button("heat-up", "启动干燥器", "color2")
        self.dryer_btn.set_size_request(-1, 50)
        self.dryer_btn.connect("clicked", self.toggle_dryer_btn)
        bottom_box.pack_start(self.dryer_btn, True, True, 0)
        
        main_box.pack_start(bottom_box, False, False, 0)
        
        self.content.add(main_box)
        self.content.show_all()
        
        # 更新状态
        self.update_status()
    
    def show_slot_dialog(self, slot):
        """为料盘设置创建超紧凑对话框"""
        # 超简约对话框框
        dialog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)  # 极小间距
        dialog_box.set_margin_left(5)   # 最小边距
        dialog_box.set_margin_right(5)
        dialog_box.set_margin_top(3)
        dialog_box.set_margin_bottom(3)
        
        # 存储当前值
        self.dialog_material = "PLA"
        self.dialog_color = [255, 255, 255]
        self.dialog_temp = 200
        
        # 材料行 - 超紧凑
        material_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        material_label = Gtk.Label(label="材料:")  # 缩短标签
        material_label.set_size_request(35, -1)  # 更小宽度
        material_row.pack_start(material_label, False, False, 0)
        
        type_combo = Gtk.ComboBoxText()
        materials = ["PLA", "ABS", "PETG", "TPU", "ASA"]  # 移除了"其他"
        for material in materials:
            type_combo.append_text(material)
        type_combo.set_active(0)
        type_combo.connect("changed", self.on_material_changed)
        material_row.pack_start(type_combo, True, True, 0)
        dialog_box.pack_start(material_row, False, False, 0)
        
        # 颜色和温度在一行以节省空间
        color_temp_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        # 颜色部分
        color_label = Gtk.Label(label="颜色:")
        color_label.set_size_request(35, -1)
        color_temp_row.pack_start(color_label, False, False, 0)
        
        # 微小颜色预览
        self.dialog_color_preview = Gtk.EventBox()
        self.dialog_color_preview.get_style_context().add_class("ace_color_preview")
        self.dialog_color_preview.set_size_request(20, 15)  # 非常小
        self.set_color_preview(self.dialog_color_preview, self.dialog_color)
        color_temp_row.pack_start(self.dialog_color_preview, False, False, 0)
        
        # 颜色选择器按钮 - 紧凑
        color_btn = self._gtk.Button("", "编辑", "color1")
        color_btn.set_size_request(50, -1)  # 固定小宽度
        color_btn.connect("clicked", self.on_color_clicked)
        color_temp_row.pack_start(color_btn, False, False, 0)
        
        # 温度部分在同一行
        temp_label = Gtk.Label(label="温度:")
        color_temp_row.pack_start(temp_label, False, False, 0)
        
        temp_btn = self._gtk.Button("", f"{self.dialog_temp}°", "color1")  # 移除了"C"
        temp_btn.set_size_request(50, -1)  # 固定小宽度
        temp_btn.connect("clicked", self.on_temp_clicked)
        color_temp_row.pack_start(temp_btn, False, False, 0)
        
        dialog_box.pack_start(color_temp_row, False, False, 0)
        
        # 存储引用用于更新
        self.dialog_color_button = color_btn
        self.dialog_temp_button = temp_btn
        
        # 空料盘选项 - 紧凑
        empty_check = Gtk.CheckButton(label="标记为空")  # 缩短标签
        dialog_box.pack_start(empty_check, False, False, 0)
        
        # 存储引用用于检查
        self.dialog_empty_check = empty_check
        
        buttons = [
            {"name": "取消", "response": Gtk.ResponseType.CANCEL},
            {"name": "应用", "response": Gtk.ResponseType.OK}
        ]
        
        def slot_response(dialog, response_id):
            logging.info(f"ACE: 设置对话框响应: {response_id}")
            try:
                if response_id == Gtk.ResponseType.OK:
                    if self.dialog_empty_check.get_active():
                        # 使用 ACE_SET_SLOT 标记为空
                        self._screen._ws.klippy.gcode_script(f"ACE_SET_SLOT INDEX={slot} EMPTY=1")
                        self._screen.show_popup_message(f"料盘 {slot} 标记为空", 1)
                    else:
                        material = self.dialog_material
                        color = f"{self.dialog_color[0]},{self.dialog_color[1]},{self.dialog_color[2]}"
                        temp = self.dialog_temp
                        
                        try:
                            # 验证值
                            if temp < 0 or temp > 300:
                                raise ValueError("温度必须在 0-300°C 之间")
                            
                            # 使用 ACE_SET_SLOT 更新料盘数据
                            cmd = f"ACE_SET_SLOT INDEX={slot} COLOR={color} MATERIAL={material} TEMP={temp}"
                            self._screen._ws.klippy.gcode_script(cmd)
                            
                            self._screen.show_popup_message(f"料盘 {slot} 已配置: {material} {temp}°C", 1)
                            
                            # 设置后刷新数据以获取更新信息
                            self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
                            
                        except ValueError as e:
                            self._screen.show_popup_message(f"错误: {e}")
                else:
                    logging.info("ACE: 设置对话框已取消")
            except Exception as e:
                logging.error(f"ACE: slot_response 中的错误: {e}")
            finally:
                # 确保对话框清理
                if hasattr(self._gtk, 'remove_dialog') and dialog:
                    self._gtk.remove_dialog(dialog)
                    logging.info("ACE: 设置对话框已关闭")
        
        self._gtk.Dialog(f"料盘 {slot} 设置", buttons, dialog_box, slot_response)
    
    def on_material_changed(self, combo):
        """处理材料组合框更改"""
        self.dialog_material = combo.get_active_text()
    
    def on_color_clicked(self, widget):
        """处理颜色选择器按钮点击"""
        def color_callback(rgb_values):
            logging.info(f"ACE: 颜色回调接收: {rgb_values}")
            self.dialog_color = rgb_values
            self.dialog_color_button.set_label("编辑")  # 保持一致的标签
            self.set_color_preview(self.dialog_color_preview, rgb_values)
        
        self.show_color_picker("选择颜色", self.dialog_color, color_callback)
    
    def on_temp_clicked(self, widget):
        """处理温度按钮点击"""
        def temp_callback(value):
            self.dialog_temp = value
            self.dialog_temp_button.set_label(f"{value}°")
        
        # 使用在对话框内有效的简单数字输入方法
        self.show_number_input("设置温度", "输入温度 (0-300°C):", 
                              self.dialog_temp, 0, 300, temp_callback)
    
    def toggle_dryer_btn(self, widget):
        """切换干燥器开/关"""
        if self.dryer_enabled:
            # 停止干燥器
            self._screen._ws.klippy.gcode_script("ACE_STOP_DRYING")
            self.dryer_btn.set_label("启动干燥器")
            self.dryer_btn.get_style_context().remove_class("color4")
            self.dryer_btn.get_style_context().add_class("color2")
            self.dryer_enabled = False
            self._screen.show_popup_message("干燥器已停止", 1)
        else:
            # 启动干燥器 - 显示温度对话框
            self.show_dryer_dialog()
    
    def show_dryer_dialog(self):
        """显示设置干燥器温度的对话框"""
        def dryer_callback(value):
            # 启动干燥器
            self._screen._ws.klippy.gcode_script(f"ACE_START_DRYING TEMP={value} DURATION=240")
            self.dryer_btn.set_label("停止干燥器")
            self.dryer_btn.get_style_context().remove_class("color2")
            self.dryer_btn.get_style_context().add_class("color4")
            self.dryer_enabled = True
            self._screen.show_popup_message(f"干燥器已在 {value}°C 启动", 1)
        
        self.show_number_input("启动干燥器", "输入干燥器温度:", 45, 35, 55, dryer_callback)
    
    def update_status(self):
        """更新 ACE 状态和料盘信息"""
        # 查询 ACE 数据
        self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
        
        # 查询当前加载索引
        self._screen._ws.klippy.gcode_script("ACE_GET_CURRENT_INDEX")
        
        # 查询自动续料状态
        self._screen._ws.klippy.gcode_script("ACE_ENDLESS_SPOOL_STATUS")
        
        # 更新加载状态
        self.update_slot_loaded_states()
    
    def process_update(self, action, data):
        """处理来自 Klipper 的更新"""
        if action == "notify_status_update":
            # 检查 saved_variables 更新
            if "saved_variables" in data:
                save_vars = data["saved_variables"]
                
                if isinstance(save_vars, dict) and "variables" in save_vars:
                    variables = save_vars["variables"]
                    if "ace_current_index" in variables:
                        new_value = int(variables["ace_current_index"])
                        logging.info(f"ACE: ace_current_index 更新为: {new_value}")
                        if new_value != self.current_loaded_slot:
                            self.current_loaded_slot = new_value
                            self.update_slot_loaded_states()
        
        if action == "notify_gcode_response":
            # 解析不同类型的 ACE 响应
            response_str = str(data).strip()
            logging.info(f"ACE: 收到 gcode 响应: {response_str}")
            
            # 查找 ACE_QUERY_SLOTS 响应 - 以 "// [" 开头
            if response_str.startswith("// [") and response_str.endswith("]"):
                try:
                    # 移除 "// " 前缀并解析 JSON
                    json_str = response_str[3:].strip()  # 移除 "// " 前缀
                    slot_data = json.loads(json_str)
                    if isinstance(slot_data, list) and len(slot_data) > 0:
                        logging.info(f"ACE: 从 ACE_QUERY_SLOTS 解析料盘数据: {slot_data}")
                        self.update_slots_from_data(slot_data)
                except json.JSONDecodeError as e:
                    logging.error(f"ACE: JSON 解码错误: {e}")
            
            # 查找 ACE_GET_CURRENT_INDEX 响应 - 简单格式如 "// 0" 或 "// -1"
            elif response_str.startswith("// ") and response_str[3:].strip().lstrip('-').isdigit():
                try:
                    # 从响应中提取索引号，如 "// 0", "// 2", 或 "// -1"
                    current_index = int(response_str[3:].strip())
                    logging.info(f"ACE: 从 ACE_GET_CURRENT_INDEX 获取当前索引: {current_index}")
                    if current_index != self.current_loaded_slot:
                        self.current_loaded_slot = current_index
                        self.update_slot_loaded_states()
                except (ValueError, IndexError) as e:
                    logging.error(f"ACE: 解析 ACE_GET_CURRENT_INDEX 响应 '{response_str}' 错误: {e}")
            
            # 查找自动续料状态响应 - 检查带 // 前缀的 "Currently enabled" 行
            elif response_str.startswith("// - Currently enabled:"):
                if "Currently enabled: True" in response_str:
                    self.endless_spool_enabled = True
                    self.endless_spool_switch.set_active(True)
                    logging.info("ACE: 自动续料当前已启用")
                elif "Currently enabled: False" in response_str:
                    self.endless_spool_enabled = False
                    self.endless_spool_switch.set_active(False)
                    logging.info("ACE: 自动续料当前已禁用")
            
            # 查找可能指示工具更改的 ACE 命令响应
            elif "ACE:" in response_str:
                logging.info(f"ACE: 命令响应: {response_str}")
                
                # 查找工具更改确认
                if "tool" in response_str.lower() and any(word in response_str.lower() for word in ["loaded", "changed", "active"]):
                    try:
                        # 尝试从响应中提取料盘号
                        import re
                        match = re.search(r'(\d+)', response_str)
                        if match:
                            new_slot = int(match.group(1))
                            if 0 <= new_slot <= 3:
                                logging.info(f"ACE: 检测到工具更改，更新到料盘 {new_slot}")
                                self.current_loaded_slot = new_slot
                                self.update_slot_loaded_states()
                    except Exception as e:
                        logging.error(f"ACE: 解析工具更改响应错误: {e}")
    
    def update_slots_from_data(self, slot_data):
        """从 ACE_QUERY_SLOTS 数据更新料盘显示"""
        logging.info(f"ACE: 从 ACE_QUERY_SLOTS 数据更新料盘: {slot_data}")
        
        for i, slot in enumerate(slot_data):
            if i < 4:  # 确保不超过我们的4个料盘
                if slot.get('status') == 'ready':
                    material = slot.get('material', 'PLA')
                    temp = slot.get('temp', 200)
                    color = slot.get('color', [255, 255, 255])
                    
                    # 存储实际料盘数据
                    self.slot_data[i] = {
                        "material": material,
                        "color": color[:],  # 复制颜色数组
                        "temp": temp,
                        "status": "ready"
                    }
                    
                    self.slot_labels[i].set_text(f"{material} {temp}°C")
                    self.set_slot_color(self.slot_color_boxes[i], color)
                    logging.info(f"ACE: 更新料盘 {i}: {material} {temp}°C, 颜色: {color}")
                else:
                    # 存储空料盘数据
                    self.slot_data[i] = {
                        "material": "PLA",
                        "color": [255, 255, 255],
                        "temp": 200,
                        "status": "empty"
                    }
                    
                    self.slot_labels[i].set_text("空")
                    self.set_slot_color(self.slot_color_boxes[i], [0, 0, 0])
                    logging.info(f"ACE: 料盘 {i} 标记为空")
        
        # 更新料盘数据后更新加载状态
        self.update_slot_loaded_states()
