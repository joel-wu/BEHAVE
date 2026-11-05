module ai_ml_audio_mel_mel_filterbank_apply (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_spectrum,
    input  logic        cfg_valid,
    input  logic        cfg_we,
    input  logic [5:0]  cfg_addr,
    input  logic [31:0] cfg_wdata,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_mel
);
    logic signed [31:0] fb [0:63];
    logic signed [67:0] sums [0:3];
    logic [3:0] freq_index;
    logic [1:0] out_index;
    logic outputting;
    wire accepted = in_valid && in_ready;
    wire config_write = accepted && cfg_valid && cfg_we;
    assign in_ready = !rst && !outputting;
    assign out_valid = outputting;
    assign out_mel = sums[out_index][63:0];
    function automatic logic [31:0] default_coefficient(input integer address);
        case (address)
            0: default_coefficient = 32'd0;
            1: default_coefficient = 32'd0;
            2: default_coefficient = 32'd0;
            3: default_coefficient = 32'd0;
            4: default_coefficient = 32'd59097;
            5: default_coefficient = 32'd6439;
            6: default_coefficient = 32'd0;
            7: default_coefficient = 32'd0;
            8: default_coefficient = 32'd13068;
            9: default_coefficient = 32'd52468;
            10: default_coefficient = 32'd0;
            11: default_coefficient = 32'd0;
            12: default_coefficient = 32'd0;
            13: default_coefficient = 32'd45623;
            14: default_coefficient = 32'd19913;
            15: default_coefficient = 32'd0;
            16: default_coefficient = 32'd0;
            17: default_coefficient = 32'd17816;
            18: default_coefficient = 32'd47720;
            19: default_coefficient = 32'd0;
            20: default_coefficient = 32'd0;
            21: default_coefficient = 32'd0;
            22: default_coefficient = 32'd59501;
            23: default_coefficient = 32'd6035;
            24: default_coefficient = 32'd0;
            25: default_coefficient = 32'd0;
            26: default_coefficient = 32'd42702;
            27: default_coefficient = 32'd22834;
            28: default_coefficient = 32'd0;
            29: default_coefficient = 32'd0;
            30: default_coefficient = 32'd25904;
            31: default_coefficient = 32'd39632;
            32: default_coefficient = 32'd0;
            33: default_coefficient = 32'd0;
            34: default_coefficient = 32'd9105;
            35: default_coefficient = 32'd56431;
            36: default_coefficient = 32'd0;
            37: default_coefficient = 32'd0;
            38: default_coefficient = 32'd0;
            39: default_coefficient = 32'd60889;
            40: default_coefficient = 32'd0;
            41: default_coefficient = 32'd0;
            42: default_coefficient = 32'd0;
            43: default_coefficient = 32'd50740;
            44: default_coefficient = 32'd0;
            45: default_coefficient = 32'd0;
            46: default_coefficient = 32'd0;
            47: default_coefficient = 32'd40592;
            48: default_coefficient = 32'd0;
            49: default_coefficient = 32'd0;
            50: default_coefficient = 32'd0;
            51: default_coefficient = 32'd30444;
            52: default_coefficient = 32'd0;
            53: default_coefficient = 32'd0;
            54: default_coefficient = 32'd0;
            55: default_coefficient = 32'd20296;
            56: default_coefficient = 32'd0;
            57: default_coefficient = 32'd0;
            58: default_coefficient = 32'd0;
            59: default_coefficient = 32'd10148;
            60: default_coefficient = 32'd0;
            61: default_coefficient = 32'd0;
            62: default_coefficient = 32'd0;
            63: default_coefficient = 32'd0;

            default: default_coefficient = 0;
        endcase
    endfunction
    always_ff @(posedge clk) begin : sequential
        integer i;
        logic signed [31:0] sample_value, coefficient;
        logic signed [63:0] product;
        if (rst) begin
            freq_index <= 0; out_index <= 0; outputting <= 0;
            for (i=0; i<64; i=i+1) fb[i] <= default_coefficient(i);
            for (i=0; i<4; i=i+1) sums[i] <= 0;
        end else begin
            if (config_write) fb[cfg_addr] <= $signed(cfg_wdata);
            if (accepted) begin
                sample_value = $signed(in_spectrum);
                for (i=0; i<4; i=i+1) begin
                    coefficient = (config_write && cfg_addr==int'(freq_index)*4+i)
                                ? $signed(cfg_wdata) : fb[int'(freq_index)*4+i];
                    product = sample_value*coefficient;
                    sums[i] <= sums[i] + {{4{product[63]}},product};
                end
                if (freq_index==15) begin
                    freq_index<=0; out_index<=0; outputting<=1;
                end else freq_index<=freq_index+1'b1;
            end else if (out_valid && out_ready) begin
                if (out_index==3) begin
                    out_index<=0; outputting<=0;
                    for (i=0; i<4; i=i+1) sums[i]<=0;
                end else out_index<=out_index+1'b1;
            end
        end
    end
endmodule
