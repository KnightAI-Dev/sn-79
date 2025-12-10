/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "MagneticField.hpp"

#include "Simulation.hpp"

#include <cmath>
#include <source_location>

//-------------------------------------------------------------------------
//-------------------------------------------------------------------------
MagneticFieldLogger::MagneticFieldLogger(const fs::path& filepath)
    : m_filepath{filepath}
{
    fs::remove(filepath);

    m_logger = std::make_unique<spdlog::logger>(
        fmt::format("MagneticFieldLogger-{}", filepath.stem().c_str()),
        std::make_unique<spdlog::sinks::basic_file_sink_st>(filepath));
    m_logger->set_level(spdlog::level::trace);
    m_logger->set_pattern("%v");

    m_logger->trace(s_header);
    m_logger->flush();
}

void MagneticFieldLogger::log(Timestamp timestamp, float totalMagnetism, const std::vector<int> field, uint32_t lastPosition)
{
    m_logger->trace(fmt::format("{},{},{},{}",timestamp, totalMagnetism, fmt::join(field, ";"),lastPosition));
    m_logger->flush();
}

//-------------------------------------------------------------------------
MagneticField::MagneticField(taosim::simulation::ISimulation* simulation, const MagneticFieldDesc& desc) noexcept 
 :  m_simulation{simulation},
    m_rows{desc.num_agents_sqrt},
    m_numAgents{m_rows*m_rows}, 
    m_field(m_numAgents, +1),
    m_alpha{desc.alpha},
    m_beta{desc.beta},
    m_J{desc.interactionCoef}
{
    m_updatePeriod = desc.updatePeriod;
    m_rng = RNG{desc.seed};
    initialize();
    m_logger = std::make_unique<MagneticFieldLogger>(desc.filepath);
}



float MagneticField::updateMagnetism() 
{
        float magnetism =  totalMagnetism();
        m_return = (magnetism -m_magnetism)/m_numAgents;
        return magnetism;
}
float MagneticField::totalMagnetism()
{
    return ranges::accumulate(m_field,0.0);
}

float MagneticField::localSum(int i, int j, float J) {
    static const std::array<std::pair<int,int>, 8> directions = {{
        { 1,  0}, {-1,  0}, { 0,  1}, { 0, -1},
        { 1,  1}, { 1, -1}, {-1,  1}, {-1, -1} 
    }};
    auto validNeighbors =
        directions | std::views::filter([&](const auto& pair) {
            int ni = i + pair.first, nj = j + pair.second;
            return ni >= 0 && ni < m_rows && nj >= 0 && nj < m_rows;
        });
    return ranges::accumulate(
        validNeighbors | std::views::transform(
        [&](const auto& d) {
        return at_field(i + d.first, j + d.second) * J;
        }),
        0.0
);
}

std::unique_ptr<MagneticField> MagneticField::fromXML(
    taosim::simulation::ISimulation* simulation, 
    pugi::xml_node node, uint64_t seed, 
    const fs::path& filepath)
{
    const float alpha = node.attribute("alpha").as_float(6);
    const float beta = node.attribute("beta").as_float(0.667);
    const auto num_agents_sqrt = node.attribute("numRows").as_uint(32); 
    const auto updatePeriod = node.attribute("updatePeriod").as_ullong(10'000'000'000);
    const auto interCoef = node.attribute("interactionCoef").as_float(1.0f);
    return std::make_unique<MagneticField>(simulation, 
        MagneticFieldDesc{
            .num_agents_sqrt=num_agents_sqrt,
            .alpha = alpha, 
            .beta = beta,
            .interactionCoef = interCoef,
            .seed = seed,
            .filepath = filepath,
            .updatePeriod = updatePeriod
            });
}
DurationComp MagneticField::getDurationComp(const std::string& basename) {
    auto [it, inserted] = m_basenameDuration.try_emplace(basename, DurationComp{.delay=0.1f, .psi=0.1f});
    auto& value = it->second;
    return value;
}
void MagneticField::insertDurationComp(const std::string& basename, DurationComp event) {
    // TODO move to header if no other changes here
    m_basenameDuration[basename] = event;
}

int MagneticField::asyncUpdate(uint32_t pos) {
    const auto [i,j] = resolvePosition(pos);
    float h_local = localSum(i,j, m_J); 
    float h_global = m_alpha * at_field(i,j)* std::abs(avgMagnetism()); 
    float local_field = h_local - h_global; 
    float probability = 1/(1+std::exp(-2.0 * m_beta *  local_field));
    float decision = BoolToSpin(std::bernoulli_distribution(probability)(m_rng));
    m_field[pos] = (decision > 0) - (decision < 0);
    
    return m_field[pos];
}

void MagneticField::logState(Timestamp timestamp, uint32_t lastPosition) {
    m_logger->log(timestamp, m_magnetism, m_field, lastPosition);
}

void MagneticField::setValAt(uint32_t pos, int val) {
    m_field[pos] = val;
    asyncUpdate(pos);
}
void MagneticField::update(Timestamp timestamp) {
            
    m_last_count++;
    m_rng.seed(std::random_device{}());
    std::vector<double> weights(m_numAgents, 1.0);
    std::discrete_distribution<uint32_t> dist(weights.begin(), weights.end());
    uint32_t num_updates = m_numAgents -1; 
    for (int j=0; j<num_updates; j++) {
        int pos = dist(m_rng);
        asyncUpdate(pos);
    }    
    m_magnetism = updateMagnetism();
    m_value = avgMagnetism();
}


//------------------------------------------------------------------------

void MagneticField::checkpointSerialize(
    rapidjson::Document& json, const std::string& key) const
{}
//-------------------------------------------------------------------------

