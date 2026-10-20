// agentes-sas en AWS: diez servicios de ECS Express Mode — type "network". Sin coordenadas a mano.
// Audiencia → ALB público → Nexo → ALB interno → Company MCP ← agentes; estado y eventos administrados
// fuera de la VPC. Generado con drawio-ai (skill drawio-aws): `node build.mjs`.
import { writeFileSync } from "node:fs";
import { Diagram } from "/Users/alvarovera/.local/lib/node_modules/drawio-ai-kit/src/builder.mjs";
import { group, frame, grid, icon, box, phantom, renderTree } from "/Users/alvarovera/.local/lib/node_modules/drawio-ai-kit/src/layout-engine.mjs";

const OUT = "./arquitectura-aws.drawio";
const d = new Diagram("network");

const tree = phantom("root", "", { dir: "row", gap: 48, align: "center", header: 0, pad: 10 }, [
  icon("users", "users", "Audiencia (navegador)"),
  group("cloud", "group_aws_cloud", "AWS · cuenta de desarrollo", { dir: "col", gap: 24 }, [
    group("region", "group_region", "us-east-1 · clúster ECS agentes-sas-dev", { dir: "row", gap: 36, align: "center" }, [
      group("vpc", "group_vpc", "VPC 10.40.0.0/16 · 2 AZ", { dir: "row", gap: 30, align: "center" }, [
        group("pub", "group_subnet", "Subredes públicas", { dir: "col", gap: 22, align: "center" }, [
          icon("alb_pub", "application_load_balancer", "ALB público (Express)"),
          icon("nexo", "fargate", "Nexo · tienda"),
          icon("nat", "nat_gateway", "NAT gateway"),
        ]),
        group("priv", "group_subnet", "Subredes privadas", { dir: "col", gap: 22, align: "center" }, [
          icon("alb_int", "application_load_balancer", "ALB interno (Express)"),
          grid("svc", null, "Servicios Express · 1 tarea", { cols: 3, gap: 16, pad: 12 }, [
            icon("agents", "fargate", "7 agentes Hermes"),
            icon("mcp", "fargate", "Company MCP"),
            icon("activity", "fargate", "agent-activity"),
          ]),
        ]),
      ]),
      phantom("managed", "", { dir: "col", gap: 24, header: 0, pad: 0, align: "left" }, [
        grid("business", null, "Eventos y estado", { cols: 3, gap: 16, pad: 12 }, [
          icon("eb", "eventbridge", "EventBridge"),
          icon("sqs", "sqs", "SQS por agente + DLQ"),
          icon("ddb", "dynamodb", "DynamoDB"),
        ]),
        grid("config", null, "Archivos y configuración", { cols: 3, gap: 16, pad: 12 }, [
          icon("s3", "s3", "S3 · transcripciones"),
          icon("appcfg", "app_config", "AppConfig · prompts"),
          icon("ecr", "ecr", "ECR · imágenes"),
        ]),
        grid("ai", null, "IA y observabilidad", { cols: 3, gap: 16, pad: 12 }, [
          icon("bedrock", "bedrock", "Bedrock"),
          icon("cw", "cloudwatch_2", "CloudWatch"),
          icon("ssm", "systems_manager", "SSM · secretos"),
        ]),
      ]),
    ]),
  ]),
]);

renderTree(d, tree, [40, 90]);
// Sin título: la slide y la página de draw.io ya lo nombran.

d.link("users", "alb_pub", "HTTPS + contraseña", { step: 1 });
d.link("alb_pub", "nexo", "", { step: 2 });
d.link("nexo", "alb_int", "place_order", { step: 3 });
d.link("alb_int", "mcp", "IAM firmado", { step: 4 });
d.link("mcp", "business", "estado y eventos", { step: 5 });
d.link("sqs", "agents", "un evento = un turno", { step: 6 });
d.link("agents", "alb_int", "herramientas", { dash: true });
d.link("agents", "ai", "inferencia", { dash: true });

const res = d.validate();
console.log("VALIDATE:", JSON.stringify({ ok: res.ok, errors: res.errors, warnings: res.warnings, advice: res.audit.advice }));
writeFileSync(new URL(OUT, import.meta.url), d.mxfile("agentes-sas en AWS"));

// Self-check tail (added by `drawio-ai scaffold`): one run = build + validate + render + issues.
import { execFileSync as __exec } from "node:child_process";
try {
  const __f = new URL(OUT, import.meta.url).pathname;
  console.log(__exec("drawio-ai", ["render", __f, "--check", "-o", __f + ".png"], { encoding: "utf8" }).trim());
} catch (e) { console.error("RENDER-SKIPPED:", String(e.message).split("\n")[0]); }
