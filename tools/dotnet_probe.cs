// 只读探测 .NET 程序集与 WMI 的小工具（.NET Framework 4 自带编译器即可构建）。
// 由 tools/dotnet_probe.py 按需编译，不随仓库分发二进制。
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Management;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

internal static class Program
{
    private static string assemblyDir = "";
    private static readonly BindingFlags Flags =
        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance |
        BindingFlags.Static | BindingFlags.DeclaredOnly;

    private static int Main(string[] args)
    {
        try { Console.OutputEncoding = Encoding.UTF8; }
        catch { }
        if (args.Length == 0) { Usage(); return 2; }
        string mode = args[0].ToLowerInvariant();
        Dictionary<string, string> opts = ParseOptions(args, 1);
        try
        {
            switch (mode)
            {
                case "constants": return Constants(opts);
                case "pinvoke": return PInvokeDump(opts);
                case "il": return IlDump(opts);
                case "wmi": return Wmi(opts);
                default: Usage(); return 2;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("失败：" + ex.Message);
            return 1;
        }
    }

    private static void Usage()
    {
        Console.Error.WriteLine("用法：");
        Console.Error.WriteLine("  dotnet_probe constants -Path <程序集> [-TypeFilter 正则] [-FieldFilter 正则] [-OutJson 文件]");
        Console.Error.WriteLine("  dotnet_probe pinvoke   -Path <程序集> [-TypeFilter 正则]");
        Console.Error.WriteLine("  dotnet_probe il        -Path <程序集> [-TypeFilter 正则] [-MethodFilter 正则]");
        Console.Error.WriteLine("  dotnet_probe wmi       -Mode guid|scan|classes [-Pattern 正则]");
    }

    private static Dictionary<string, string> ParseOptions(string[] args, int start)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (int i = start; i < args.Length; i++)
        {
            string key = args[i];
            if (!key.StartsWith("-", StringComparison.Ordinal)) continue;
            key = key.TrimStart('-');
            string value = "true";
            if (i + 1 < args.Length && !args[i + 1].StartsWith("-", StringComparison.Ordinal))
                value = args[++i];
            result[key] = value;
        }
        return result;
    }

    private static string Opt(Dictionary<string, string> opts, string name, string fallback)
    {
        string value;
        return opts.TryGetValue(name, out value) ? value : fallback;
    }

    // PowerShell 的 -match 默认不区分大小写；这里保持一致，否则会漏掉 ecBt1RSOC 这类小写字段。
    private static bool Matches(string input, string pattern)
    {
        return Regex.IsMatch(input, pattern, RegexOptions.IgnoreCase);
    }

    private static string DefaultAssembly()
    {
        string[] candidates = {
            @"C:\Program Files\OEM\CreatorCenter\UniwillService\MyControlCenter\GCUService.exe",
            @"C:\Program Files (x86)\OEM\CreatorCenter\UniwillService\MyControlCenter\GCUService.exe"
        };
        foreach (string path in candidates)
            if (File.Exists(path)) return path;
        return candidates[0];
    }

    private static Assembly LoadAssembly(string path)
    {
        string full = Path.GetFullPath(path);
        assemblyDir = Path.GetDirectoryName(full);
        AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve += ResolveDependency;
        try { return Assembly.ReflectionOnlyLoadFrom(full); }
        catch { return Assembly.LoadFrom(full); }
    }

    private static Assembly ResolveDependency(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name;
        foreach (string ext in new[] { ".dll", ".exe" })
        {
            string candidate = Path.Combine(assemblyDir, name + ext);
            if (!File.Exists(candidate)) continue;
            try { return Assembly.ReflectionOnlyLoadFrom(candidate); }
            catch { }
            try { return Assembly.LoadFrom(candidate); }
            catch { }
        }
        return null;
    }

    private static Type[] LoadableTypes(Assembly assembly)
    {
        try { return assembly.GetTypes(); }
        catch (ReflectionTypeLoadException ex)
        {
            var list = new List<Type>();
            foreach (Type type in ex.Types)
                if (type != null) list.Add(type);
            return list.ToArray();
        }
    }

    private static object Normalize(object value)
    {
        if (value == null) return null;
        Type type = value.GetType();
        if (type == typeof(byte) || type == typeof(sbyte) || type == typeof(short) ||
            type == typeof(ushort) || type == typeof(int) || type == typeof(uint) ||
            type == typeof(long))
            return Convert.ToInt64(value, CultureInfo.InvariantCulture);
        if (type == typeof(ulong))
        {
            ulong u = (ulong)value;
            return u <= (ulong)long.MaxValue
                ? (object)(long)u
                : u.ToString(CultureInfo.InvariantCulture);
        }
        if (type == typeof(float) || type == typeof(double) || type == typeof(decimal))
            return Convert.ToDouble(value, CultureInfo.InvariantCulture);
        if (type == typeof(char)) return value.ToString();
        if (type == typeof(string) || type == typeof(bool)) return value;
        return value.ToString();
    }

    private static string TypeName(Type type)
    {
        try { return type.FullName ?? type.Name; }
        catch { return type.Name; }
    }

    private static int Constants(Dictionary<string, string> opts)
    {
        string path = Opt(opts, "Path", DefaultAssembly());
        string typeFilter = Opt(opts, "TypeFilter",
            "Define|EC|Ec|Acpi|ACPI|IOCTL|Fan|FAN|Serv|Client|Power|Thermal|Temp|Spec|CMD|Mode|Smart|Apc|Gpu|RGB|Battery|Charge");
        string fieldFilter = Opt(opts, "FieldFilter",
            "IOCTL|EC|FAN|TMP|TEMP|PL1|PL2|MODE|ADDR|REG|SMART|CMD|INDEX|OFFSET|READ|WRITE|TURBO|OFFICE|BALANCE|CHARGE|BATTERY|TGP|SM");
        string outJson = Opt(opts, "OutJson", null);
        if (!File.Exists(path))
        {
            Console.Error.WriteLine("找不到程序集：" + path);
            return 1;
        }
        Console.WriteLine("# 目标：" + path + "（只读元数据）");
        Assembly assembly = LoadAssembly(path);
        Type[] types = LoadableTypes(assembly);
        var collected = new Dictionary<string, object>();
        int enumCount = 0;
        int constCount = 0;

        foreach (Type type in types)
        {
            if (!Matches(type.Name, typeFilter)) continue;
            string fullName = TypeName(type);
            if (type.IsEnum)
            {
                string[] names;
                try { names = Enum.GetNames(type); }
                catch { continue; }
                if (names.Length == 0) continue;
                Console.WriteLine();
                Console.WriteLine("[enum] " + fullName);
                var values = new Dictionary<string, object>();
                foreach (string name in names)
                {
                    object value = null;
                    try { value = type.GetField(name).GetRawConstantValue(); }
                    catch { }
                    if (value == null)
                    {
                        Console.WriteLine(string.Format("    {0,-42} = ?", name));
                        continue;
                    }
                    object normalized = Normalize(value);
                    long number;
                    if (normalized is long)
                    {
                        number = (long)normalized;
                        Console.WriteLine(string.Format("    {0,-42} = {1}  (0x{1:X})", name, number));
                    }
                    else Console.WriteLine(string.Format("    {0,-42} = {1}", name, normalized));
                    values[name] = normalized;
                }
                collected[fullName] = new Dictionary<string, object> {
                    { "kind", "enum" }, { "values", values }
                };
                enumCount++;
                continue;
            }

            FieldInfo[] fields;
            try { fields = type.GetFields(Flags); }
            catch { continue; }
            var lines = new List<string>();
            var fieldValues = new Dictionary<string, object>();
            foreach (FieldInfo field in fields)
            {
                if (!Matches(field.Name, fieldFilter)) continue;
                if (!field.IsLiteral) continue;
                object value = null;
                try { value = field.GetRawConstantValue(); }
                catch { }
                if (value == null) continue;
                object normalized = Normalize(value);
                long number;
                string shown;
                if (normalized is long)
                {
                    number = (long)normalized;
                    shown = string.Format("{0}  (0x{0:X})", number);
                }
                else if (normalized is string)
                    shown = "\"" + normalized + "\"";
                else
                    shown = Convert.ToString(normalized, CultureInfo.InvariantCulture);
                fieldValues[field.Name] = normalized;
                lines.Add(string.Format("    {0,-42} = {1}", field.Name, shown));
            }
            if (lines.Count == 0) continue;
            Console.WriteLine();
            Console.WriteLine("[const] " + fullName);
            foreach (string line in lines) Console.WriteLine(line);
            collected[fullName] = new Dictionary<string, object> {
                { "kind", "const" }, { "values", fieldValues }
            };
            constCount += lines.Count;
        }

        Console.WriteLine();
        Console.WriteLine("# 枚举类型 " + enumCount + " 个，常量 " + constCount + " 条");
        if (!string.IsNullOrEmpty(outJson))
        {
            var payload = new Dictionary<string, object> {
                { "source", path },
                { "generated", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") },
                { "note", "OEM 私有定义，本机生成、本机使用，不入库、不再分发" },
                { "types", collected }
            };
            var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
            string json = serializer.Serialize(payload);
            File.WriteAllText(outJson, json, new UTF8Encoding(false));
            Console.WriteLine("# 已写出 JSON：" + outJson + "（" + json.Length + " 字符）");
        }
        return 0;
    }

    private static bool IsDllImport(MethodInfo method)
    {
        try
        {
            foreach (CustomAttributeData attribute in CustomAttributeData.GetCustomAttributes(method))
                if (attribute.Constructor.DeclaringType.Name == "DllImportAttribute")
                    return true;
        }
        catch { }
        return false;
    }

    private static string DllName(MethodInfo method)
    {
        try
        {
            foreach (CustomAttributeData attribute in CustomAttributeData.GetCustomAttributes(method))
            {
                if (attribute.Constructor.DeclaringType.Name != "DllImportAttribute") continue;
                if (attribute.ConstructorArguments.Count > 0)
                    return Convert.ToString(attribute.ConstructorArguments[0].Value);
            }
        }
        catch { }
        return "";
    }

    private static string FormatMethod(Type type, MethodInfo method)
    {
        var parameters = new List<string>();
        foreach (ParameterInfo parameter in method.GetParameters())
        {
            string prefix = parameter.IsOut ? "out " : (parameter.ParameterType.IsByRef ? "ref " : "");
            string paramType = "?";
            try { paramType = parameter.ParameterType.Name.TrimEnd('&'); }
            catch { }
            parameters.Add(prefix + paramType + " " + parameter.Name);
        }
        string returnType = "?";
        try { returnType = method.ReturnType.Name; }
        catch { }
        string dll = DllName(method);
        if (dll.Length > 0) dll = " [DllImport " + dll + "]";
        return string.Format("{0,-30} {1,-8} {2}({3}){4}",
            type.Name, returnType, method.Name, string.Join(", ", parameters.ToArray()), dll);
    }

    private static int PInvokeDump(Dictionary<string, string> opts)
    {
        string path = Opt(opts, "Path", DefaultAssembly());
        string typeFilter = Opt(opts, "TypeFilter", "ACPI|EC|Fan|Hardware|Win32|Native|Driver|Thermal|Temp|Power|Sm");
        if (!File.Exists(path))
        {
            Console.Error.WriteLine("找不到程序集：" + path);
            return 1;
        }
        Assembly assembly = LoadAssembly(path);
        Type[] types = LoadableTypes(assembly);
        Console.WriteLine("目标：" + path);
        Console.WriteLine("工作目录：" + assemblyDir + "（让加载器能解析同目录依赖）");
        int pinvokes = 0;
        int matched = 0;

        Console.WriteLine();
        Console.WriteLine("=== 全部 DllImport（原生互操作入口）===");
        foreach (Type type in types)
        {
            MethodInfo[] methods;
            try { methods = type.GetMethods(Flags); }
            catch { continue; }
            foreach (MethodInfo method in methods)
            {
                if (!IsDllImport(method)) continue;
                try { Console.WriteLine(FormatMethod(type, method)); pinvokes++; }
                catch { }
            }
        }

        Console.WriteLine();
        Console.WriteLine("=== 类型名匹配 /" + typeFilter + "/ 的方法（找 EC 包装层）===");
        foreach (Type type in types)
        {
            if (!Matches(type.Name, typeFilter)) continue;
            MethodInfo[] methods;
            try { methods = type.GetMethods(Flags); }
            catch { continue; }
            Console.WriteLine(string.Format("--- {0}（{1} 个方法）", TypeName(type), methods.Length));
            foreach (MethodInfo method in methods)
            {
                try { Console.WriteLine("    " + FormatMethod(type, method)); matched++; }
                catch { Console.WriteLine("    " + method.Name + "(参数解析失败)"); }
            }
        }
        Console.WriteLine();
        Console.WriteLine("DllImport 共 " + pinvokes + " 条；匹配类型的方法共 " + matched + " 条");
        return 0;
    }

    private static readonly Dictionary<byte, string[]> Marks = new Dictionary<byte, string[]> {
        { 0x20, new[] { "int32", "4" } }, { 0x1F, new[] { "int8", "1" } },
        { 0x72, new[] { "string", "4" } }, { 0x28, new[] { "method", "4" } },
        { 0x6F, new[] { "method", "4" } }, { 0x73, new[] { "method", "4" } },
        { 0x29, new[] { "method", "4" } }, { 0x7E, new[] { "field", "4" } },
        { 0x80, new[] { "field", "4" } }, { 0x7B, new[] { "field", "4" } },
        { 0x7D, new[] { "field", "4" } }, { 0xD0, new[] { "member", "4" } }
    };

    private static readonly Dictionary<byte, string> SmallConstants = new Dictionary<byte, string> {
        { 0x15, "-1" }, { 0x16, "0" }, { 0x17, "1" }, { 0x18, "2" }, { 0x19, "3" },
        { 0x1A, "4" }, { 0x1B, "5" }, { 0x1C, "6" }, { 0x1D, "7" }, { 0x1E, "8" }
    };

    private static string ResolveToken(Module module, int token, string kind)
    {
        try
        {
            if (kind == "method")
            {
                MethodBase method = module.ResolveMethod(token);
                return method.DeclaringType.Name + "::" + method.Name;
            }
            if (kind == "field")
            {
                FieldInfo field = module.ResolveField(token);
                return field.DeclaringType.Name + "::" + field.Name;
            }
            if (kind == "string") return "\"" + module.ResolveString(token) + "\"";
            if (kind == "member")
            {
                MemberInfo member = module.ResolveMember(token);
                return member.DeclaringType.Name + "::" + member.Name;
            }
        }
        catch { }
        return string.Format("token:0x{0:X8}", token);
    }

    private static int IlDump(Dictionary<string, string> opts)
    {
        string path = Opt(opts, "Path", DefaultAssembly());
        string typeFilter = Opt(opts, "TypeFilter", @"MyECIO\.AcpiCtrl|MyECIO\.MyEcCtrl");
        string methodFilter = Opt(opts, "MethodFilter", ".");
        if (!File.Exists(path))
        {
            Console.Error.WriteLine("找不到程序集：" + path);
            return 1;
        }
        Assembly assembly = LoadAssembly(path);
        Type[] types = LoadableTypes(assembly);
        foreach (Type type in types)
        {
            string fullName = TypeName(type);
            if (!Matches(fullName, typeFilter) && !Matches(type.Name, typeFilter)) continue;
            MethodInfo[] methods;
            try { methods = type.GetMethods(Flags); }
            catch { continue; }
            foreach (MethodInfo method in methods)
            {
                if (!Matches(method.Name, methodFilter)) continue;
                MethodBody body;
                try { body = method.GetMethodBody(); }
                catch { continue; }
                if (body == null) continue;
                byte[] il;
                try { il = body.GetILAsByteArray(); }
                catch { continue; }
                if (il == null || il.Length == 0) continue;

                var parameters = new List<string>();
                try
                {
                    foreach (ParameterInfo parameter in method.GetParameters())
                        parameters.Add(parameter.ParameterType.Name + " " + parameter.Name);
                }
                catch { parameters.Add("?"); }
                string returnType = "?";
                try { returnType = method.ReturnType.Name; }
                catch { }
                Console.WriteLine();
                Console.WriteLine(string.Format("==== {0}.{1}({2}) -> {3}   IL {4} 字节",
                    type.Name, method.Name, string.Join(", ", parameters.ToArray()),
                    returnType, il.Length));
                Console.WriteLine("  原始 IL（每行 16 字节）：");
                for (int offset = 0; offset < il.Length; offset += 16)
                {
                    int end = Math.Min(offset + 15, il.Length - 1);
                    var hex = new List<string>();
                    for (int i = offset; i <= end; i++) hex.Add(il[i].ToString("X2"));
                    Console.WriteLine(string.Format("    {0:X4}  {1}", offset, string.Join(" ", hex.ToArray())));
                }
                Console.WriteLine("  有序标记：");
                for (int i = 0; i < il.Length; i++)
                {
                    byte op = il[i];
                    string small;
                    if (SmallConstants.TryGetValue(op, out small))
                    {
                        Console.WriteLine(string.Format("    偏移 {0,4}: ldc.i4.{1}", i, small));
                        continue;
                    }
                    string[] mark;
                    if (!Marks.TryGetValue(op, out mark)) continue;
                    string kind = mark[0];
                    int operandLength = int.Parse(mark[1], CultureInfo.InvariantCulture);
                    if (i + operandLength >= il.Length) continue;
                    if (kind == "int8")
                    {
                        Console.WriteLine(string.Format("    偏移 {0,4}: ldc.i4.s {1}", i, (int)il[i + 1]));
                        continue;
                    }
                    uint value = BitConverter.ToUInt32(il, i + 1);
                    if (kind == "int32")
                    {
                        Console.WriteLine(string.Format("    偏移 {0,4}: ldc.i4 {1}  (0x{1:X8})", i, value));
                        continue;
                    }
                    string label = kind == "method" ? "call" : (kind == "field" ? "field" : (kind == "string" ? "ldstr" : "ldtoken"));
                    Console.WriteLine(string.Format("    偏移 {0,4}: {1,-7} {2}", i, label,
                        ResolveToken(method.Module, (int)value, kind)));
                }
            }
        }
        return 0;
    }

    private static ManagementScope WmiScope(string path)
    {
        var scope = new ManagementScope(@"\\.\" + path);
        scope.Connect();
        return scope;
    }

    private static string Str(object value)
    {
        return value == null ? "" : Convert.ToString(value);
    }

    private static string HardwareIds(ManagementObject obj)
    {
        try
        {
            string[] values = obj["HardwareID"] as string[];
            return values == null ? "" : string.Join(",", values);
        }
        catch { return ""; }
    }

    private static int Wmi(Dictionary<string, string> opts)
    {
        string mode = Opt(opts, "Mode", "classes").ToLowerInvariant();
        if (mode == "guid") return WmiGuid();
        if (mode == "scan") return WmiScan();
        if (mode == "classes") return WmiClasses(Opt(opts, "Pattern", "EC|ACPI|Thermal|Battery|Energy"));
        Console.Error.WriteLine("未知 WMI 模式：" + mode);
        return 2;
    }

    private static int WmiGuid()
    {
        string[] guids = {
            "B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B",
            "46C93E13-EE9B-4262-8488-563BCA757FEF",
            "FA78E245-2C0F-4CA1-91CF-15F34E474850"
        };
        ManagementScope scope = WmiScope("root/wmi");
        foreach (string guid in guids)
        {
            Console.WriteLine("--- " + guid);
            try
            {
                var cls = new ManagementClass(scope, new ManagementPath(guid), null);
                cls.Get();
                string super = "";
                try { super = Str(cls.SystemProperties["__SUPERCLASS"].Value); }
                catch { }
                Console.WriteLine("    class: FOUND  super=" + super);
                foreach (MethodData method in cls.Methods)
                {
                    var parameters = new List<string>();
                    string returnType = "?";
                    try
                    {
                        foreach (PropertyData parameter in method.InParameters.Properties)
                            parameters.Add(parameter.Type + " " + parameter.Name);
                        foreach (PropertyData parameter in method.OutParameters.Properties)
                        {
                            if (parameter.Name == "ReturnValue")
                            {
                                returnType = parameter.Type.ToString();
                                continue;
                            }
                            parameters.Add("[out] " + parameter.Type + " " + parameter.Name);
                        }
                    }
                    catch { }
                    Console.WriteLine("    method: " + method.Name + "(" +
                        string.Join(", ", parameters.ToArray()) + ") -> " + returnType);
                }
                foreach (PropertyData property in cls.Properties)
                    Console.WriteLine("    prop: " + property.Type + " " + property.Name);
                int count = 0;
                var names = new List<string>();
                foreach (ManagementObject instance in cls.GetInstances())
                {
                    count++;
                    try { names.Add(Str(instance["InstanceName"])); }
                    catch { }
                }
                Console.WriteLine("    instances: " + count);
                foreach (string name in names) Console.WriteLine("      " + name);
            }
            catch (ManagementException)
            {
                Console.WriteLine("    class: NOT FOUND");
            }
            catch (Exception ex)
            {
                Console.WriteLine("    class: ERROR " + ex.Message);
            }
        }
        return 0;
    }

    private static List<string> WmiClassNames(ManagementScope scope)
    {
        var names = new List<string>();
        var searcher = new ManagementObjectSearcher(scope, new ObjectQuery("SELECT * FROM meta_class"));
        foreach (ManagementObject obj in searcher.Get())
        {
            string name = Str(obj["__CLASS"]);
            if (name.Length > 0) names.Add(name);
        }
        names.Sort(StringComparer.OrdinalIgnoreCase);
        return names;
    }

    private static int WmiClasses(string pattern)
    {
        ManagementScope scope = WmiScope("root/wmi");
        List<string> all = WmiClassNames(scope);
        var matched = new List<string>();
        foreach (string name in all)
            if (Matches(name, pattern)) matched.Add(name);
        Console.WriteLine("--- root\\WMI 里与 EC / ACPI / 热区相关的类（只读查询）---");
        if (matched.Count == 0) Console.WriteLine("（没取到，或该类命名空间不可读）");
        foreach (string name in matched) Console.WriteLine(name);
        Console.WriteLine();
        Console.WriteLine("--- 可能带方法的 WMI 类（EC 读写候选）---");
        bool any = false;
        foreach (string name in matched)
        {
            if (!Matches(name, "EC|ACPI")) continue;
            try
            {
                var cls = new ManagementClass(scope, new ManagementPath(name), null);
                cls.Get();
                var methods = new List<string>();
                foreach (MethodData method in cls.Methods) methods.Add(method.Name);
                Console.WriteLine(name + " : " + string.Join(",", methods.ToArray()));
                any = true;
            }
            catch { }
        }
        if (!any) Console.WriteLine("（无）");
        return 0;
    }

    private static int WmiScan()
    {
        ManagementScope cim = WmiScope("root/cimv2");
        ManagementScope wmi = WmiScope("root/wmi");
        const string Standard = "PNP0A08|PNP0C0C|PNP0C0D|PNP0C0E|PNP0C0F|PNP0C14|PNP0C0A|PNP0C01|PNP0C02|PNP0C04|PNP0C09|PNP0100|PNP0103|PNP0200|PNP0800|PNP0B00|PNP0303|PNP0F13|PNP0501|PNP0400|PNP0C06|PNP0C07|PNP0C08|PNP0000|PNP0C0B|PNP0D80|PNP0E00";

        Console.WriteLine();
        Console.WriteLine("=== ACPI\\PNP0C14 / WMI 映射设备 ===");
        foreach (ManagementObject obj in new ManagementObjectSearcher(cim,
            new ObjectQuery("SELECT * FROM Win32_PnPEntity WHERE PNPDeviceID LIKE 'ACPI%'")).Get())
        {
            string id = Str(obj["PNPDeviceID"]);
            string name = Str(obj["Name"]);
            string hid = HardwareIds(obj);
            if (hid.Contains("PNP0C14") || hid.Contains("WMI") || name.Contains("WMI"))
                Console.WriteLine("  " + Str(obj["Status"]) + "  " + id + "  " + name + "  [" + hid + "]");
        }

        Console.WriteLine();
        Console.WriteLine("=== 非微软 ACPI 设备（按 HardwareID 过滤掉标准 PNP 码）===");
        int shown = 0;
        foreach (ManagementObject obj in new ManagementObjectSearcher(cim,
            new ObjectQuery("SELECT * FROM Win32_PnPEntity WHERE PNPDeviceID LIKE 'ACPI%'")).Get())
        {
            string hid = HardwareIds(obj);
            if (hid.Length == 0 || Matches(hid, Standard)) continue;
            Console.WriteLine("  " + Str(obj["Status"]) + "  " + Str(obj["PNPDeviceID"]) + "  " +
                Str(obj["Name"]) + "  [" + hid + "]");
            if (++shown >= 60) break;
        }

        List<string> classes = WmiClassNames(wmi);
        Console.WriteLine();
        Console.WriteLine("=== root/wmi 里名字像 GUID 的类（ACPI WMI 数据块）===");
        int guidCount = 0;
        foreach (string name in classes)
        {
            if (!Matches(name, "^[0-9A-Fa-f]{8}-([0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$")) continue;
            guidCount++;
            try
            {
                int count = 0;
                foreach (ManagementObject instance in new ManagementClass(wmi,
                    new ManagementPath(name), null).GetInstances()) count++;
                if (count > 0) Console.WriteLine("  PRESENT  " + name + "  instances=" + count);
            }
            catch { }
        }
        Console.WriteLine("  GUID 类数量: " + guidCount);

        Console.WriteLine();
        Console.WriteLine("=== root/wmi 里名字带 OEM 味道的类 ===");
        const string OemPattern = "Tongfang|Uniwill|UW|GCU|Mechrevo|Clevo|Hotkey|HotKey|Oem|OEM|MIFS|Fan|Mode|Keyboard|KBL|RGB|Light|LightBar|EC|Batt|Charge|Turbo|Silent|Gaming|Creator";
        foreach (string name in classes)
        {
            if (!Matches(name, OemPattern)) continue;
            try
            {
                int count = 0;
                foreach (ManagementObject instance in new ManagementClass(wmi,
                    new ManagementPath(name), null).GetInstances()) count++;
                if (count > 0) Console.WriteLine("  HAS-INSTANCE(" + count + ")  " + name);
                else Console.WriteLine("  class-only             " + name);
            }
            catch { Console.WriteLine("  class-only             " + name); }
        }

        Console.WriteLine();
        Console.WriteLine("=== 本机服务里 OEM 相关（只看状态，不动它）===");
        foreach (ManagementObject service in new ManagementObjectSearcher(cim,
            new ObjectQuery("SELECT * FROM Win32_Service")).Get())
        {
            string name = Str(service["Name"]);
            string display = Str(service["DisplayName"]);
            if (!Matches(name + " " + display, "GCU|Uniwill|UW|Mechrevo|Tongfang|Creator|OEM")) continue;
            Console.WriteLine("  " + Str(service["State"]) + "  " + Str(service["StartMode"]) +
                "  " + name + "  " + display);
        }
        return 0;
    }
}
