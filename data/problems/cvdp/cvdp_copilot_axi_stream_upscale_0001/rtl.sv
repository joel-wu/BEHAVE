module axis_upscale (
    input logic clk,
    input logic resetn,
    input logic dfmt_enable,
    input logic dfmt_type,
    input logic dfmt_se,
    input logic s_axis_valid,
    input logic [23:0] s_axis_data,
    output logic s_axis_ready,
    input logic m_axis_ready,
    output logic m_axis_valid,
    output logic [31:0] m_axis_data
);
    wire converted_msb = s_axis_data[23] ^ (dfmt_enable & dfmt_type);
    wire extension = dfmt_enable & dfmt_se & converted_msb;
    wire [31:0] converted = {{8{extension}}, converted_msb, s_axis_data[22:0]};
    assign s_axis_ready = resetn && (!m_axis_valid || m_axis_ready);
    always_ff @(posedge clk) begin
        if (!resetn) begin
            m_axis_valid <= 0;
            m_axis_data <= 0;
        end else if (s_axis_ready) begin
            m_axis_valid <= s_axis_valid;
            if (s_axis_valid) m_axis_data <= converted;
        end
    end
endmodule
