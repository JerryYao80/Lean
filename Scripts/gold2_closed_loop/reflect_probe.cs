// ReflectProbe: loads a candidate G3 dll + reflects whether the candidate
// class overrides BuildRiskModels (and does NOT override Initialize).
//
// This is the second fallback (Task 7) when pythonnet runtime reflect is
// unavailable (no Mono / libcoreclr binding in the host env). The probe is
// built on-demand by ``_reflect_interface_ok_probe`` and run via
// ``dotnet reflect_probe.dll <dll> <class> <candidate_bin_dir>``.
//
// The third arg is the candidate bin dir (where copy-local deps landed
// during the candidate .csproj build). The probe uses AssemblyResolve to
// pull NodaTime + QuantConnect.*.dll from there + the Algorithm.CSharp
// bin + Launcher bin, so the candidate type + its base chain load cleanly.

using System;
using System.IO;
using System.Reflection;

internal static class ReflectProbe
{
    private static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: reflect_probe <dll> <class> [candidate_bin_dir]");
            return 2;
        }
        string dllPath = args[0];
        string className = args[1];
        string candidateBinDir = args.Length >= 3 ? args[2] : Path.GetDirectoryName(dllPath) ?? "";

        AppDomain.CurrentDomain.AssemblyResolve += (sender, e) =>
        {
            string shortName = e.Name.Split(',')[0];
            string[] candidates = new[]
            {
                Path.Combine(candidateBinDir, shortName + ".dll"),
                // Algorithm.CSharp/bin/Debug (where the proof strategy dll lives)
                "/home/project/hope/Lean/.claude/worktrees/gold2-closed-loop-proof/Algorithm.CSharp/bin/Debug/" + shortName + ".dll",
                // Launcher/bin/Debug (where NodaTime etc. land)
                "/home/project/hope/Lean/.claude/worktrees/gold2-closed-loop-proof/Launcher/bin/Debug/" + shortName + ".dll",
            };
            foreach (string p in candidates)
            {
                if (File.Exists(p))
                {
                    try { return Assembly.LoadFrom(p); } catch { }
                }
            }
            return null;
        };

        Assembly asm;
        try
        {
            asm = Assembly.LoadFrom(dllPath);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("Assembly.LoadFrom failed: " + ex.Message);
            Console.WriteLine("RESULT:TYPE_NOT_FOUND");
            return 3;
        }

        Type t = asm.GetType(className, throwOnError: false);
        if (t == null)
        {
            string fq = "QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction." + className;
            t = asm.GetType(fq, throwOnError: false);
        }
        if (t == null)
        {
            Console.WriteLine("RESULT:TYPE_NOT_FOUND");
            return 3;
        }

        // BuildRiskModels is `protected virtual` on the base; the candidate
        // must OVERRIDE it. Reflect: the method's DeclaringType must be the
        // candidate class itself (not the base).
        MethodInfo bm = t.GetMethod("BuildRiskModels",
            BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        if (bm == null)
        {
            Console.WriteLine("RESULT:NO_OVERRIDE");
            return 1;
        }
        bool overridesBM = bm.DeclaringType == t;

        // Initialize is `public override` on the BASE (it overrides
        // QCAlgorithm.Initialize). The candidate must NOT override it again.
        // Reflect: the Initialize method's DeclaringType must NOT be the
        // candidate class.
        MethodInfo init = t.GetMethod("Initialize",
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        bool overridesInit = init != null && init.DeclaringType == t;

        Console.WriteLine("OverridesBM:" + overridesBM + " OverridesInit:" + overridesInit);
        Console.WriteLine(overridesBM && !overridesInit ? "RESULT:OK" : "RESULT:FAIL");
        return 0;
    }
}
