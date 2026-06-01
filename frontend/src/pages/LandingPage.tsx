import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, useScroll, useTransform } from 'framer-motion';
import { ArrowRight, Code2, Cpu, LineChart, Shield, Zap } from 'lucide-react';

import '../styles/LandingPage.css';

const TypewriterText = ({ texts }: { texts: string[] }) => {
  const [index, setIndex] = useState(0);
  const [text, setText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const currentText = texts[index];

    if (isDeleting) {
      timer = setTimeout(() => {
        setText(currentText.substring(0, text.length - 1));
        if (text.length === 0) {
          setIsDeleting(false);
          setIndex((prev) => (prev + 1) % texts.length);
        }
      }, 40);
    } else {
      timer = setTimeout(() => {
        setText(currentText.substring(0, text.length + 1));
        if (text.length === currentText.length) {
          setTimeout(() => setIsDeleting(true), 2500);
        }
      }, 80);
    }

    return () => clearTimeout(timer);
  }, [text, isDeleting, index, texts]);

  return (
    <span className="text-gradient inline-flex items-center">
      {text}
      <motion.span
        animate={{ opacity: [1, 0] }}
        transition={{ repeat: Infinity, duration: 0.8, ease: 'linear' }}
        style={{
          display: 'inline-block',
          width: '4px',
          backgroundColor: '#38BDF8',
          marginLeft: '4px',
          height: '1em',
          verticalAlign: 'middle',
          borderRadius: '2px',
        }}
      />
    </span>
  );
};

// Animation Variants
const fadeInUp = {
  hidden: { opacity: 0, y: 40 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.8, ease: [0.16, 1, 0.3, 1] as [number, number, number, number] },
  },
};

const staggerContainer = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.15 },
  },
};

export function LandingPage() {
  const navigate = useNavigate();
  const { scrollYProgress } = useScroll();
  const mockupY = useTransform(scrollYProgress, [0, 1], [0, -100]);
  const bgY = useTransform(scrollYProgress, [0, 1], [0, 200]);

  return (
    <div className="landing-layout">
      {/* Background Parallax */}
      <motion.div className="hero-background-glow" style={{ y: bgY }} />

      {/* Navigation */}
      <motion.nav
        className="landing-nav"
        initial={{ y: -100 }}
        animate={{ y: 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] as [number, number, number, number] }}
      >
        <div className="landing-nav-content">
          <div className="landing-logo">
            <img
              src="/jarvis-logo.png"
              alt="Jarvis Studio Logo"
              style={{ height: '32px', width: 'auto' }}
            />
            <span style={{ fontWeight: 700, letterSpacing: '0.5px' }}>Jarvis Studio</span>
          </div>
          <div className="landing-nav-links">
            <a href="#features">Features</a>
            <a href="#solutions">Solutions</a>
            <a href="#developers">Developers</a>
          </div>
          <div className="landing-nav-actions">
            <button className="btn-secondary" onClick={() => navigate('/control-center')}>
              Login
            </button>
            <button className="btn-primary" onClick={() => navigate('/control-center')}>
              Get Started <ArrowRight size={16} />
            </button>
          </div>
        </div>
      </motion.nav>

      <main>
        {/* Hero Section */}
        <section className="hero">
          <motion.div
            className="hero-content"
            initial="hidden"
            animate="visible"
            variants={staggerContainer}
          >
            <motion.div variants={fadeInUp} className="hero-badge">
              Enterprise AI Data Gateway
            </motion.div>
            <motion.h1 variants={fadeInUp} className="hero-title">
              Query Your Database <br />
              <TypewriterText
                texts={[
                  'in Natural Language',
                  'with Plain English',
                  'using AI Models',
                  'without writing SQL',
                ]}
              />
            </motion.h1>
            <motion.p variants={fadeInUp} className="hero-subtitle">
              The production-ready Text-to-SQL layer for your enterprise. Connect your data, train
              the semantic layer, and give your teams instant answers with enterprise-grade
              governance and monitoring.
            </motion.p>
            <motion.div variants={fadeInUp} className="hero-cta">
              <button className="btn-primary large" onClick={() => navigate('/control-center')}>
                Start Building Free <ArrowRight size={20} />
              </button>
              <button className="btn-outline large">Book a Demo</button>
            </motion.div>

            <motion.div variants={fadeInUp} className="hero-stats">
              <div className="stat-item">
                <span className="stat-value">99.9%</span>
                <span className="stat-label">Uptime SLA</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">50ms</span>
                <span className="stat-label">Avg Latency</span>
              </div>
              <div className="stat-item">
                <span className="stat-value">SOC2</span>
                <span className="stat-label">Compliant</span>
              </div>
            </motion.div>
          </motion.div>

          <motion.div
            className="hero-visual"
            initial={{ opacity: 0, x: 50, rotateY: 15, rotateX: 5 }}
            animate={{ opacity: 1, x: 0, rotateY: -5, rotateX: 5 }}
            transition={{
              duration: 1.2,
              ease: [0.16, 1, 0.3, 1] as [number, number, number, number],
            }}
            style={{ y: mockupY }}
          >
            <div className="mockup-window">
              <div className="mockup-header">
                <div className="mockup-dots">
                  <span className="dot red"></span>
                  <span className="dot yellow"></span>
                  <span className="dot green"></span>
                </div>
                <div className="mockup-title">Query Console</div>
              </div>
              <div className="mockup-body">
                <motion.div
                  className="chat-bubble user"
                  initial={{ opacity: 0, scale: 0.9, y: 10 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  transition={{ delay: 0.8, duration: 0.5 }}
                >
                  Show me top 5 customers by MRR in Q3
                </motion.div>
                <motion.div
                  className="chat-bubble system"
                  initial={{ opacity: 0, scale: 0.9, y: 10 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  transition={{ delay: 1.6, duration: 0.5 }}
                >
                  <div className="sql-code">
                    <span className="keyword">SELECT</span> customer_name, <br />
                    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span className="function">SUM</span>
                    (mrr) <span className="keyword">AS</span> total_mrr
                    <br />
                    <span className="keyword">FROM</span> subscriptions
                    <br />
                    <span className="keyword">WHERE</span> quarter ={' '}
                    <span className="string">'Q3'</span>
                    <br />
                    <span className="keyword">GROUP BY</span> customer_name
                    <br />
                    <span className="keyword">ORDER BY</span> total_mrr{' '}
                    <span className="keyword">DESC</span>
                    <br />
                    <span className="keyword">LIMIT</span> 5;
                  </div>
                  <motion.div
                    className="query-result"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 2.2, duration: 0.5 }}
                  >
                    <LineChart size={18} className="text-primary" /> Query executed successfully in
                    45ms.
                  </motion.div>
                </motion.div>
              </div>
            </div>
            <div className="glow-effect"></div>
          </motion.div>
        </section>

        {/* Logos Section */}
        <motion.section
          className="logos-section"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-100px' }}
          variants={staggerContainer}
        >
          <motion.p variants={fadeInUp}>TRUSTED BY DATA TEAMS AT LEADING COMPANIES</motion.p>
          <div className="logos-grid">
            {['Acme Corp', 'GlobalBank', 'TechFlow', 'DataSync', 'HealthTech'].map((logo) => (
              <motion.div key={logo} variants={fadeInUp} className="logo-placeholder">
                {logo}
              </motion.div>
            ))}
          </div>
        </motion.section>

        {/* Features Section */}
        <section id="features" className="features-section">
          <motion.div
            className="section-header"
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true, margin: '-100px' }}
            variants={staggerContainer}
          >
            <motion.h2 variants={fadeInUp}>Everything you need for production TextToSQL</motion.h2>
            <motion.p variants={fadeInUp}>
              A complete gateway that handles schema translation, query generation, caching, and
              safety.
            </motion.p>
          </motion.div>

          <motion.div
            className="features-grid"
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true, margin: '-50px' }}
            variants={staggerContainer}
          >
            {[
              {
                icon: Zap,
                title: 'Instant Schema Sync',
                desc: 'Automatically synchronize your database schemas, tables, and column metadata. Add descriptions to improve LLM accuracy.',
              },
              {
                icon: Shield,
                title: 'Enterprise Governance',
                desc: 'Row-level security, query parsing, and malicious intent detection to ensure your data stays safe and secure.',
              },
              {
                icon: Cpu,
                title: 'Model Agnostic',
                desc: 'Plug in OpenAI, Anthropic, Gemini, or host your own fine-tuned open-source models for ultimate flexibility and privacy.',
              },
              {
                icon: Code2,
                title: 'Developer SDKs',
                desc: 'Integrate Text2SQL capabilities into your own products with our React, Python, and Node.js SDKs in minutes.',
              },
            ].map((feat, i) => (
              <motion.div
                key={i}
                className="feature-card"
                variants={fadeInUp}
                whileHover={{ y: -8, transition: { duration: 0.2 } }}
              >
                <div className="feature-icon">
                  <feat.icon />
                </div>
                <h3>{feat.title}</h3>
                <p>{feat.desc}</p>
              </motion.div>
            ))}
          </motion.div>
        </section>

        {/* CTA Section */}
        <motion.section
          className="cta-section"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-100px' }}
          variants={fadeInUp}
        >
          <div className="cta-content">
            <h2>Ready to give your data a voice?</h2>
            <p>Join thousands of teams building the next generation of data-driven applications.</p>
            <motion.button
              className="btn-primary large"
              onClick={() => navigate('/control-center')}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              Start Building Now
            </motion.button>
          </div>
        </motion.section>
      </main>

      <footer className="landing-footer">
        <div className="footer-content">
          <div className="footer-brand">
            <div className="landing-logo">
              <img
                src="/jarvis-logo.png"
                alt="Jarvis Studio Logo"
                style={{ height: '24px', width: 'auto' }}
              />
              <span style={{ fontWeight: 700, letterSpacing: '0.5px' }}>Jarvis Studio</span>
            </div>
            <p className="footer-desc">
              The enterprise API gateway for deploying Text-to-SQL models in production.
            </p>
          </div>
          <div className="footer-links">
            <div className="footer-col">
              <h4>Product</h4>
              <a href="#">Features</a>
              <a href="#">Pricing</a>
              <a href="#">Integrations</a>
            </div>
            <div className="footer-col">
              <h4>Resources</h4>
              <a href="#">Documentation</a>
              <a href="#">API Reference</a>
              <a href="#">Blog</a>
            </div>
            <div className="footer-col">
              <h4>Company</h4>
              <a href="#">About Us</a>
              <a href="#">Careers</a>
              <a href="#">Contact</a>
            </div>
          </div>
        </div>
        <div className="footer-bottom">
          <p>&copy; {new Date().getFullYear()} Jarvis Studio. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
