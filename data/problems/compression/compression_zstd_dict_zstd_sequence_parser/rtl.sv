module compression_zstd_dict_zstd_sequence_parser (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_byte0,
    input  logic [7:0]   in_byte1,
    input  logic [7:0]   in_byte2,
    input  logic [7:0]   in_byte3,
    input  logic [7:0]   in_byte4,
    input  logic [7:0]   in_byte5,
    input  logic [7:0]   in_byte6,
    input  logic [7:0]   in_byte7,
    input  logic [5:0]   in_size,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_nbSeq,
    output logic [1:0]   out_LLtype,
    output logic [1:0]   out_OFtype,
    output logic [1:0]   out_MLtype,
    output logic [3:0]   out_header_size,
    output logic         out_has_error,
    output logic [2:0]   out_error_code
);

logic busy;

logic [31:0] result_nbSeq;
logic [1:0]  result_LLtype;
logic [1:0]  result_OFtype;
logic [1:0]  result_MLtype;
logic [3:0]  result_header_size;
logic        result_has_error;
logic [2:0]  result_error_code;

wire input_accepted;
wire output_consumed;

logic [7:0] b0;
logic [7:0] b1;
logic [7:0] b2;
logic [7:0] b3;

logic [7:0] x;
logic [31:0] nbSeq_calc;
logic [1:0] ip_calc;
logic [7:0] m_calc;

always_comb begin
    b0 = in_byte0[7:0];
    b1 = in_byte1[7:0];
    b2 = in_byte2[7:0];
    b3 = in_byte3[7:0];

    x = b0;
    nbSeq_calc = 32'd0;
    ip_calc = 2'd0;
    m_calc = 8'd0;

    result_nbSeq = 32'd0;
    result_LLtype = 2'd0;
    result_OFtype = 2'd0;
    result_MLtype = 2'd0;
    result_header_size = 4'd0;
    result_has_error = 1'b0;
    result_error_code = 3'd0;

    if (in_size < 6'd1) begin
        result_has_error = 1'b1;
        result_error_code = 3'd1;
    end else begin
        if (x <= 8'h7F) begin
            nbSeq_calc = {24'd0, x};
            ip_calc = 2'd1;
        end else if (x == 8'hFF) begin
            if (in_size < 6'd3) begin
                result_has_error = 1'b1;
                result_error_code = 3'd1;
            end else begin
                nbSeq_calc = {16'd0, b1} + ({16'd0, b2} << 8) + 32'h00007F00;
                ip_calc = 2'd3;
            end
        end else begin
            if (in_size < 6'd2) begin
                result_has_error = 1'b1;
                result_error_code = 3'd1;
            end else begin
                nbSeq_calc = (({24'd0, x} - 32'h00000080) << 8) + {24'd0, b1};
                ip_calc = 2'd2;
            end
        end

        if (!result_has_error) begin
            if (nbSeq_calc == 32'd0) begin
                if ({4'd0, ip_calc} != in_size) begin
                    result_has_error = 1'b1;
                    result_error_code = 3'd2;
                end else begin
                    result_header_size = {2'd0, ip_calc};
                end
            end else begin
                if (in_size < ({4'd0, ip_calc} + 6'd1)) begin
                    result_has_error = 1'b1;
                    result_error_code = 3'd1;
                end else begin
                    if (ip_calc == 2'd1) begin
                        m_calc = b1;
                    end else if (ip_calc == 2'd2) begin
                        m_calc = b2;
                    end else begin
                        m_calc = b3;
                    end

                    if ((m_calc & 8'h03) != 8'd0) begin
                        result_has_error = 1'b1;
                        result_error_code = 3'd2;
                    end else begin
                        result_nbSeq = nbSeq_calc;
                        result_LLtype = m_calc[7:6];
                        result_OFtype = m_calc[5:4];
                        result_MLtype = m_calc[3:2];
                        result_header_size = {1'b0, ip_calc} + 4'd1;
                    end
                end
            end
        end
    end
end

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        busy            <= 1'b0;
        out_nbSeq       <= 32'd0;
        out_LLtype      <= 2'd0;
        out_OFtype      <= 2'd0;
        out_MLtype      <= 2'd0;
        out_header_size <= 4'd0;
        out_has_error   <= 1'b0;
        out_error_code  <= 3'd0;
    end else if (input_accepted) begin
        busy            <= 1'b1;
        out_nbSeq       <= result_nbSeq;
        out_LLtype      <= result_LLtype;
        out_OFtype      <= result_OFtype;
        out_MLtype      <= result_MLtype;
        out_header_size <= result_header_size;
        out_has_error   <= result_has_error;
        out_error_code  <= result_error_code;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
