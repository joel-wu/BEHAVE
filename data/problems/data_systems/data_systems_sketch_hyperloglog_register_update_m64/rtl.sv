module data_systems_sketch_hyperloglog_register_update_m64 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_hash,
    input  logic [383:0] in_registers,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [383:0] out_registers
);

    logic        busy;
    logic [383:0] out_registers_next;

    wire input_accepted;
    wire output_consumed;

    logic [5:0]  j;
    logic [25:0] w;
    logic [5:0]  r;
    logic [5:0]  old_reg;
    logic [5:0]  new_reg;
    logic [383:0] mask;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        j = in_hash[31:26];
        w = in_hash[25:0];

        r = 6'd27;
        if (w[25])       r = 6'd1;
        else if (w[24])  r = 6'd2;
        else if (w[23])  r = 6'd3;
        else if (w[22])  r = 6'd4;
        else if (w[21])  r = 6'd5;
        else if (w[20])  r = 6'd6;
        else if (w[19])  r = 6'd7;
        else if (w[18])  r = 6'd8;
        else if (w[17])  r = 6'd9;
        else if (w[16])  r = 6'd10;
        else if (w[15])  r = 6'd11;
        else if (w[14])  r = 6'd12;
        else if (w[13])  r = 6'd13;
        else if (w[12])  r = 6'd14;
        else if (w[11])  r = 6'd15;
        else if (w[10])  r = 6'd16;
        else if (w[9])   r = 6'd17;
        else if (w[8])   r = 6'd18;
        else if (w[7])   r = 6'd19;
        else if (w[6])   r = 6'd20;
        else if (w[5])   r = 6'd21;
        else if (w[4])   r = 6'd22;
        else if (w[3])   r = 6'd23;
        else if (w[2])   r = 6'd24;
        else if (w[1])   r = 6'd25;
        else if (w[0])   r = 6'd26;

        old_reg = in_registers[(6*j) +: 6];
        if (old_reg >= r) new_reg = old_reg;
        else              new_reg = r;

        mask = 384'({378'd0, 6'h3F}) << (6 * j);
        out_registers_next = (in_registers & ~mask) | ({{378{1'b0}}, new_reg} << (6 * j));
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy          <= 1'b0;
            out_registers <= 384'b0;
        end else if (input_accepted) begin
            busy          <= 1'b1;
            out_registers <= out_registers_next;
        end else if (output_consumed) begin
            busy          <= 1'b0;
            out_registers <= 384'b0;
        end
    end

endmodule